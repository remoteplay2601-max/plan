import os
import re
import unicodedata
from datetime import date, time
from io import BytesIO

import pandas as pd
import streamlit as st


REQUIRED_COLS = [
    "Job",
    "MK",
    "ISO",
    "Operation Description1",
    "OperationCode",
    "CustomFieldName",
    "CustomFieldValue",
    "ItemCode",
    "StepOrder",
    "BomVersionId",
]

DATE_FIELD = "DateTermine"
DEFAULT_TIME = time(15, 0)


def has_value(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    return str(val).strip() != ""


def load_excel(file_or_path):
    xls = pd.ExcelFile(file_or_path, engine="openpyxl")
    sheet_name = xls.sheet_names[0]
    df = pd.read_excel(xls, sheet_name=sheet_name, engine="openpyxl")
    return df, sheet_name


def normalize_text(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def natural_sort_key_joint(value, orig_index):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return (1, orig_index)
    text = str(value).strip()
    match = re.match(r"^([A-Za-z]+)\s*(\d+)$", text)
    if match:
        return (0, match.group(1).lower(), int(match.group(2)))
    match = re.match(r"^([A-Za-z]+).*?(\d+)", text)
    if match:
        return (0, match.group(1).lower(), int(match.group(2)))
    return (1, orig_index)


def clean_df(df):
    df_clean = df.copy()
    df_clean["_orig_index"] = df_clean.index

    mask_filled = df_clean["CustomFieldValue"].apply(has_value)

    target_fields = {"diametre", "materiel", "posoudurecorrige", "sch", "type"}
    op_norm = df_clean["OperationCode"].apply(normalize_text)
    field_norm = df_clean["CustomFieldName"].apply(normalize_text)
    mask_ass = (op_norm == "ass") & (field_norm.isin(target_fields))

    df_clean = df_clean[~(mask_filled | mask_ass)].copy()
    return df_clean


def passthrough_df(df):
    df_copy = df.copy()
    df_copy["_orig_index"] = df_copy.index
    return df_copy


def format_datetime(date_value, time_value):
    month = date_value.strftime("%b")
    hour = time_value.hour
    minute = time_value.minute
    ampm = "AM" if hour < 12 else "PM"
    hour12 = hour % 12
    if hour12 == 0:
        hour12 = 12
    return f"{month} {date_value.day:02d} {date_value.year}  {hour12}:{minute:02d}{ampm}"


def parse_genius_datetime(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    match = re.match(
        r"^([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})\s{2}(\d{1,2}):(\d{2})(AM|PM)$",
        text,
    )
    if not match:
        return None
    month_abbr = match.group(1).title()
    month_map = {
        "Jan": 1,
        "Feb": 2,
        "Mar": 3,
        "Apr": 4,
        "May": 5,
        "Jun": 6,
        "Jul": 7,
        "Aug": 8,
        "Sep": 9,
        "Oct": 10,
        "Nov": 11,
        "Dec": 12,
    }
    month = month_map.get(month_abbr)
    if not month:
        return None
    day = int(match.group(2))
    year = int(match.group(3))
    hour = int(match.group(4))
    minute = int(match.group(5))
    ampm = match.group(6)
    if ampm == "PM" and hour != 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    return date(year, month, day), time(hour, minute)


def apply_updates(df_clean, updates):
    if not updates:
        return df_clean
    df_updated = df_clean.copy()
    for idx, value in updates.items():
        if value is None:
            df_updated.at[idx, "CustomFieldValue"] = ""
        else:
            df_updated.at[idx, "CustomFieldValue"] = value
    return df_updated


def save_to_disk(df_clean, path, sheet_name, original_columns):
    if not path:
        raise ValueError("Missing save path.")
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    export_df = df_clean.drop(columns=["_orig_index"], errors="ignore")
    if original_columns:
        export_df = export_df[original_columns]
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        export_df.to_excel(writer, sheet_name=sheet_name, index=False)


def export_bytes(df_clean, sheet_name, original_columns):
    export_df = df_clean.drop(columns=["_orig_index"], errors="ignore")
    if original_columns:
        export_df = export_df[original_columns]
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        export_df.to_excel(writer, sheet_name=sheet_name, index=False)
    return output.getvalue()


def export_genius_bytes(df_clean, sheet_name, original_columns):
    genius_df = df_clean[df_clean["CustomFieldValue"].apply(has_value)].copy()
    genius_df = genius_df.drop(columns=["_orig_index"], errors="ignore")
    if original_columns:
        genius_df = genius_df[original_columns]
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        genius_df.to_excel(writer, sheet_name=sheet_name, index=False)
    return output.getvalue()


def unique_in_order(values):
    seen = set()
    ordered = []
    for val in values:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        if val not in seen:
            seen.add(val)
            ordered.append(val)
    return ordered


def sorted_group(df_group):
    df_sorted = df_group.copy()
    df_sorted["_sort_key"] = df_sorted.apply(
        lambda row: natural_sort_key_joint(
            row["Operation Description1"], row["_orig_index"]
        ),
        axis=1,
    )
    df_sorted = df_sorted.sort_values(by="_sort_key", kind="mergesort")
    return df_sorted.drop(columns=["_sort_key"])


def build_ui(df_clean, selected_job):
    updates = {}
    df_job = df_clean[df_clean["Job"] == selected_job].copy()
    if df_job.empty:
        st.info("Aucune ligne pour ce Job apres epuration.")
        return updates

    op_codes = unique_in_order(df_job["OperationCode"].tolist())
    for op_code in op_codes:
        st.subheader(f"OperationCode: {op_code}")
        df_op = df_job[df_job["OperationCode"] == op_code].copy()

        date_mask = df_op["CustomFieldName"].apply(
            lambda v: normalize_text(v) == normalize_text(DATE_FIELD)
        )
        df_date = df_op[date_mask]
        if not df_date.empty:
            existing_value = (
                df_date["CustomFieldValue"]
                .dropna()
                .astype(str)
                .map(str.strip)
            )
            existing_value = existing_value[existing_value != ""].head(1)
            default_date = date.today()
            default_time = DEFAULT_TIME
            if not existing_value.empty:
                parsed = parse_genius_datetime(existing_value.iloc[0])
                if parsed:
                    default_date, default_time = parsed
            apply_key = f"apply_dt_{selected_job}_{op_code}"
            default_apply = not existing_value.empty
            apply_value = st.checkbox(
                f"Renseigner DateTermine pour {op_code}",
                value=default_apply,
                key=apply_key,
            )
            date_key = f"dt_date_{selected_job}_{op_code}"
            time_key = f"dt_time_{selected_job}_{op_code}"
            date_value = st.date_input(
                f"DateTermine - date ({op_code})",
                value=default_date,
                key=date_key,
            )
            time_value = st.time_input(
                f"DateTermine - heure ({op_code})",
                value=default_time,
                key=time_key,
            )
            if apply_value:
                formatted = format_datetime(date_value, time_value)
                for idx in df_date.index:
                    if df_clean.at[idx, "CustomFieldValue"] != formatted:
                        updates[idx] = formatted

        other_fields = df_op[~date_mask]
        field_names = unique_in_order(other_fields["CustomFieldName"].tolist())
        for field_name in field_names:
            st.markdown(f"**{field_name}**")
            df_field = other_fields[other_fields["CustomFieldName"] == field_name]
            df_field = sorted_group(df_field)
            for idx, row in df_field.iterrows():
                joint_label = row["Operation Description1"]
                if joint_label is None or (
                    isinstance(joint_label, float) and pd.isna(joint_label)
                ):
                    joint_label = "(Sans joint)"
                current_value = row["CustomFieldValue"]
                if current_value is None or (
                    isinstance(current_value, float) and pd.isna(current_value)
                ):
                    current_value = ""
                key = f"val_{idx}"
                col_left, col_right = st.columns([1, 3])
                col_left.write(str(joint_label))
                new_value = col_right.text_input(
                    f"{field_name} {idx}",
                    value=str(current_value),
                    key=key,
                    label_visibility="collapsed",
                )
                if new_value != str(current_value):
                    updates[idx] = new_value
        st.divider()
    return updates


def try_tk_open_file():
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.update()
        path = filedialog.askopenfilename(
            title="Open Excel file",
            filetypes=[("Excel files", "*.xlsx")],
        )
        root.destroy()
        return path if path else None
    except Exception:
        return None


def try_tk_save_file(default_name):
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.update()
        path = filedialog.asksaveasfilename(
            title="Save Excel file",
            defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[("Excel files", "*.xlsx")],
        )
        root.destroy()
        return path if path else None
    except Exception:
        return None


def normalize_save_path(path, default_name):
    if not path:
        return ""
    path = os.path.expanduser(path)
    if os.path.isdir(path):
        path = os.path.join(path, default_name)
    if not path.lower().endswith(".xlsx"):
        path += ".xlsx"
    return path


def init_session_state():
    defaults = {
        "df_clean": None,
        "sheet_name": None,
        "save_path": None,
        "selected_job": None,
        "updates": {},
        "original_columns": None,
        "loaded_source_id": None,
        "job_changed": False,
        "mode": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def main():
    st.set_page_config(page_title="Qualifab Genius Input", layout="wide")
    init_session_state()

    st.title("Qualifab - Saisie CustomFieldValue")
    st.write(
        "Choisissez un mode, chargez un fichier Excel, puis renseignez les valeurs."
    )

    mode = st.radio(
        "Mode de travail",
        ["Nouveau document", "Continuer (reprendre la derniere fois)"],
    )
    if st.session_state["mode"] != mode:
        st.session_state["mode"] = mode
        st.session_state["df_clean"] = None
        st.session_state["loaded_source_id"] = None
        st.session_state["selected_job"] = None
        st.session_state["updates"] = {}
        st.session_state["save_path"] = None

    file_or_path = None
    source_id = None
    save_path = st.session_state.get("save_path")

    if mode == "Nouveau document":
        st.subheader("1) Charger un fichier brut")
        if st.button("Ouvrir un fichier local (dialogue Windows)"):
            path = try_tk_open_file()
            if path:
                st.session_state["new_input_path"] = path
            else:
                st.warning(
                    "Dialogue indisponible. Utilisez l'upload ou le champ ci-dessous."
                )
        input_path = st.session_state.get("new_input_path")
        uploaded = st.file_uploader(
            "Uploader un fichier .xlsx",
            type=["xlsx"],
            key="uploader_new",
        )
        if input_path:
            file_or_path = input_path
            source_id = f"path::{os.path.abspath(input_path)}"
        elif uploaded is not None:
            file_or_path = uploaded
            source_id = f"upload::{uploaded.name}::{uploaded.size}"
        else:
            st.info("Selectionnez un fichier Excel pour commencer.")

        if file_or_path is not None:
            st.subheader("2) Choisir l'emplacement de sauvegarde")
            default_name = (
                os.path.basename(file_or_path)
                if isinstance(file_or_path, str)
                else uploaded.name
            )
            if st.button("Choisir l'emplacement (dialogue Windows)"):
                chosen = try_tk_save_file(default_name)
                if chosen:
                    st.session_state["save_path"] = chosen
                else:
                    st.warning(
                        "Dialogue indisponible. Utilisez le champ de chemin ci-dessous."
                    )
            default_path = os.path.join(os.getcwd(), default_name)
            path_input = st.text_input(
                "Chemin complet du fichier de travail (.xlsx)",
                value=st.session_state.get("save_path") or default_path,
            )
            save_path = normalize_save_path(path_input, default_name)
            st.session_state["save_path"] = save_path
            st.caption(f"Dossier par defaut: {os.getcwd()}")

    else:
        st.subheader("1) Reprendre un fichier existant")
        if st.button("Ouvrir un fichier local (dialogue Windows)"):
            path = try_tk_open_file()
            if path:
                st.session_state["cont_input_path"] = path
            else:
                st.warning(
                    "Dialogue indisponible. Utilisez l'upload ou le champ ci-dessous."
                )
        input_path = st.session_state.get("cont_input_path")
        uploaded = st.file_uploader(
            "Uploader un fichier .xlsx",
            type=["xlsx"],
            key="uploader_continue",
        )
        if input_path:
            file_or_path = input_path
            source_id = f"path::{os.path.abspath(input_path)}"
            save_path = input_path
            st.session_state["save_path"] = save_path
        elif uploaded is not None:
            file_or_path = uploaded
            source_id = f"upload::{uploaded.name}::{uploaded.size}"
            default_name = uploaded.name
            default_path = os.path.join(os.getcwd(), default_name)
            path_input = st.text_input(
                "Chemin de sauvegarde (.xlsx)",
                value=st.session_state.get("save_path") or default_path,
            )
            save_path = normalize_save_path(path_input, default_name)
            st.session_state["save_path"] = save_path
            st.caption(
                "Impossible de recuperer le chemin original depuis l'upload."
            )
        else:
            st.error("Choisissez un fichier pour continuer.")

    if file_or_path is not None and save_path:
        if st.session_state["loaded_source_id"] != source_id:
            try:
                df_raw, sheet_name = load_excel(file_or_path)
            except Exception as exc:
                st.error(f"Erreur lors du chargement: {exc}")
                return
            missing = [c for c in REQUIRED_COLS if c not in df_raw.columns]
            if missing:
                st.error(f"Colonnes manquantes: {', '.join(missing)}")
                return
            df_clean = clean_df(df_raw)
            st.session_state["df_clean"] = df_clean
            st.session_state["sheet_name"] = sheet_name
            st.session_state["original_columns"] = list(df_raw.columns)
            st.session_state["loaded_source_id"] = source_id
            st.session_state["selected_job"] = None
            st.session_state["updates"] = {}

        st.success(f"Fichier de travail: {st.session_state['save_path']}")

    df_clean = st.session_state.get("df_clean")
    if df_clean is None:
        return

    jobs = unique_in_order(df_clean["Job"].tolist())
    if not jobs:
        st.warning("Aucun Job disponible apres epuration.")
        return

    def on_job_change():
        st.session_state["job_changed"] = True

    if st.session_state["selected_job"] in jobs:
        default_index = jobs.index(st.session_state["selected_job"])
    else:
        default_index = 0

    selected_job = st.selectbox(
        "Job",
        jobs,
        index=default_index,
        key="job_select",
        on_change=on_job_change,
    )
    st.session_state["selected_job"] = selected_job

    updates = build_ui(df_clean, selected_job)
    st.session_state["updates"] = updates
    df_updated = apply_updates(df_clean, updates)
    st.session_state["df_clean"] = df_updated

    if st.session_state.get("job_changed"):
        if st.session_state.get("save_path"):
            save_to_disk(
                st.session_state["df_clean"],
                st.session_state["save_path"],
                st.session_state["sheet_name"],
                st.session_state["original_columns"],
            )
            st.success("Sauvegarde automatique effectuee.")
        else:
            st.warning("Chemin de sauvegarde manquant pour l'auto-save.")
        st.session_state["job_changed"] = False

    col_save, col_export, col_genius = st.columns(3)
    if col_save.button("Sauvegarder maintenant"):
        try:
            save_to_disk(
                st.session_state["df_clean"],
                st.session_state["save_path"],
                st.session_state["sheet_name"],
                st.session_state["original_columns"],
            )
            st.success("Sauvegarde terminee.")
        except Exception as exc:
            st.error(f"Erreur de sauvegarde: {exc}")

    export_data = export_bytes(
        st.session_state["df_clean"],
        st.session_state["sheet_name"],
        st.session_state["original_columns"],
    )
    export_name = (
        os.path.basename(st.session_state["save_path"])
        if st.session_state.get("save_path")
        else "export.xlsx"
    )
    col_export.download_button(
        "Exporter Excel",
        data=export_data,
        file_name=export_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    genius_data = export_genius_bytes(
        st.session_state["df_clean"],
        st.session_state["sheet_name"],
        st.session_state["original_columns"],
    )
    genius_name = (
        f"genius_{export_name}" if export_name else "genius_export.xlsx"
    )
    col_genius.download_button(
        "Exporter Genius",
        data=genius_data,
        file_name=genius_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    main()
