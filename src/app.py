from __future__ import annotations

import io
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime as DateTime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import pytesseract
import streamlit as st
from PIL import Image, ImageOps
from pytesseract import Output


@dataclass
class OCRWord:
    text: str
    x_center: float
    y_center: float
    confidence: float


@dataclass
class OCRLine:
    text: str
    x_center: float
    y_center: float
    confidence: float
    words: list[OCRWord]


TIME_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}:\d{2})(?!\d)")
DATE_RE = re.compile(r"(?<!\d)(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4})(?!\d)")
ID_RE = re.compile(r"(?<!\d)((?:\d[ \t]*){15,22})(?!\d)")
SHORT_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})(?!\d)")
TRANSACTION_ID_RE = re.compile(r"(?<!\d)(\d{8,14})(?!\d)")
MONEY_RE = re.compile(
    r"(?<![\d:])(-?\s*(?:[$€₫]\s*)?(?:\d+)?[.,]\d{1,4})(?!\d)"
)
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATABASE_PATH = DATA_DIR / "records.sqlite3"


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def fold_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", normalize_text(value)).casefold()
    return "".join(char for char in decomposed if not unicodedata.combining(char)).replace("đ", "d")


def parse_ocr_lines(image: Image.Image) -> list[OCRLine]:
    """Run local Tesseract OCR and preserve token positions for row parsing."""
    data = pytesseract.image_to_data(
        image,
        lang="vie+eng",
        config="--oem 3 --psm 6",
        output_type=Output.DICT,
    )
    width, height = image.size
    groups: dict[tuple[int, int, int, int], list[OCRWord]] = {}

    count = len(data.get("text", []))
    for index in range(count):
        text = normalize_text(data["text"][index])
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (ValueError, TypeError):
            confidence = -1.0
        if confidence < 0:
            continue

        left = int(data["left"][index])
        top = int(data["top"][index])
        word_width = int(data["width"][index])
        word_height = int(data["height"][index])
        word = OCRWord(
            text=text,
            x_center=(left + word_width / 2) / max(width, 1),
            y_center=(top + word_height / 2) / max(height, 1),
            confidence=confidence / 100.0,
        )
        key = (
            int(data["page_num"][index]),
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        groups.setdefault(key, []).append(word)

    lines: list[OCRLine] = []
    for words in groups.values():
        words.sort(key=lambda item: item.x_center)
        text = " ".join(word.text for word in words)
        lines.append(
            OCRLine(
                text=text,
                x_center=sum(word.x_center for word in words) / len(words),
                y_center=sum(word.y_center for word in words) / len(words),
                confidence=sum(word.confidence for word in words) / len(words),
                words=words,
            )
        )
    return sorted(lines, key=lambda line: (line.y_center, line.x_center))


def extract_ticket_id(text: str) -> str | None:
    match = ID_RE.search(text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return digits if 15 <= len(digits) <= 22 else None


def extract_money(text: str) -> list[float]:
    values: list[float] = []
    for match in MONEY_RE.finditer(text):
        normalized = re.sub(r"[^\d,.-]", "", match.group(1)).replace(",", ".")
        if normalized.startswith("."):
            normalized = "0" + normalized
        elif normalized.startswith("-."):
            normalized = normalized.replace("-.", "-0.", 1)
        try:
            values.append(float(normalized))
        except ValueError:
            continue
    return values


def parse_sc8882_records(lines: list[OCRLine]) -> list[dict[str, Any]]:
    time_anchors = [
        (line, match.group(1))
        for line in lines
        for match in TIME_RE.finditer(line.text)
    ]
    id_anchors = [
        (line, ticket_id)
        for line in lines
        if (ticket_id := extract_ticket_id(line.text)) is not None
    ]

    used_id_lines: set[int] = set()
    anchors: list[tuple[OCRLine | None, str | None, OCRLine | None, str | None]] = []

    # Keep duplicate displayed times as separate records; pair each with the
    # nearest ticket ID below it.
    for time_line, time_value in time_anchors:
        candidates = [
            (id_line, ticket_id)
            for id_line, ticket_id in id_anchors
            if id(id_line) not in used_id_lines
            and id_line.y_center >= time_line.y_center - 0.005
        ]
        nearest = min(
            candidates,
            key=lambda item: abs(item[0].y_center - time_line.y_center),
            default=None,
        )
        if nearest and nearest[0].y_center - time_line.y_center <= 0.10:
            used_id_lines.add(id(nearest[0]))
            anchors.append((time_line, time_value, nearest[0], nearest[1]))
        else:
            anchors.append((time_line, time_value, None, None))

    # Keep an ID-only candidate when OCR missed its timestamp.
    for id_line, ticket_id in id_anchors:
        if id(id_line) not in used_id_lines:
            anchors.append((None, None, id_line, ticket_id))

    anchors.sort(
        key=lambda item: (item[0] or item[2]).y_center if (item[0] or item[2]) else 1.0
    )

    records: list[dict[str, Any]] = []
    id_line_ids = {id(line) for line, _ in id_anchors}
    for time_line, time_value, id_line, ticket_id in anchors:
        anchor_line = time_line or id_line
        if anchor_line is None:
            continue
        start_y = (time_line.y_center if time_line else anchor_line.y_center - 0.10) - 0.075
        end_y = (id_line.y_center if id_line else anchor_line.y_center + 0.07) + 0.012

        row_lines = [
            line
            for line in lines
            if start_y <= line.y_center <= end_y
            and (id(line) not in id_line_ids or line is id_line)
        ]
        row_words = [word for line in row_lines for word in line.words]
        row_text = " ".join(line.text for line in row_lines)

        date_match = DATE_RE.search(row_text)
        date_value = date_match.group(1) if date_match else None
        date_time = f"{date_value} {time_value}" if date_value and time_value else time_value

        stake_values: list[tuple[float, float]] = []
        result_values: list[tuple[float, float]] = []
        for word in row_words:
            normalized_word = fold_text(word.text)
            for value in extract_money(word.text):
                if word.x_center >= 0.72 or "thang" in normalized_word or "thua" in normalized_word:
                    result_values.append((word.y_center, value))
                elif word.x_center >= 0.30:
                    stake_values.append((word.y_center, value))

        stake_values.sort(key=lambda item: item[0])
        result_values.sort(key=lambda item: item[0])

        status = next(
            (line.text for line in row_lines if "thanh toan" in fold_text(line.text)),
            None,
        )
        game = next(
            (
                line.text
                for line in row_lines
                if line.y_center < (time_line.y_center if time_line else anchor_line.y_center)
                and line.x_center < 0.40
                and ("pg" in fold_text(line.text) or "quyet chien" in fold_text(line.text))
            ),
            None,
        )
        confidence = (
            sum(line.confidence for line in row_lines) / len(row_lines)
            if row_lines
            else 0.0
        )

        record = {
            "source_type": "sc8882",
            "datetime": date_time,
            "game": game or "",
            "bet_amount": stake_values[0][1] if stake_values else None,
            "valid_bet": stake_values[1][1] if len(stake_values) > 1 else None,
            "net_result": result_values[0][1] if result_values else None,
            "ticket_id": ticket_id or "",
            "status": status or "",
            "needs_review": False,
            "ocr_confidence": round(confidence, 3),
        }
        record["needs_review"] = any(
            [
                not record["datetime"],
                not record["ticket_id"],
                record["bet_amount"] is None,
                record["valid_bet"] is None,
                record["net_result"] is None,
                not record["status"],
                confidence < 0.70,
            ]
        )
        records.append(record)

    return records


def detect_source_type(lines: list[OCRLine]) -> str:
    """Classify the screenshot layout before extracting its rows."""
    header_text = " ".join(fold_text(line.text) for line in lines[:12])
    if "giao dich" in header_text and (
        "cuoc" in header_text or "loi" in header_text
    ):
        return "alo789"
    transaction_line_count = sum(
        bool(TRANSACTION_ID_RE.search(line.text)) for line in lines
    )
    if transaction_line_count >= 2 and not any(
        extract_ticket_id(line.text) for line in lines
    ):
        return "alo789"
    return "sc8882"


def parse_alo789_records(lines: list[OCRLine]) -> list[dict[str, Any]]:
    """Parse the Alo789 history layout with time, transaction, bet and profit columns."""
    time_anchors = [
        (line, match.group(1))
        for line in lines
        for match in TIME_RE.finditer(line.text)
    ]
    if not time_anchors:
        return []

    time_anchors.sort(key=lambda item: item[0].y_center)
    all_text = " ".join(line.text for line in lines)
    year_match = re.search(r"\b(20\d{2})\b", all_text)
    year = year_match.group(1) if year_match else str(DateTime.now().year)
    records: list[dict[str, Any]] = []

    for index, (time_line, time_value) in enumerate(time_anchors):
        next_time_y = (
            time_anchors[index + 1][0].y_center
            if index + 1 < len(time_anchors)
            else min(1.0, time_line.y_center + 0.10)
        )
        row_lines = [
            line
            for line in lines
            if time_line.y_center - 0.015 <= line.y_center < next_time_y - 0.012
        ]
        row_words = [word for line in row_lines for word in line.words]
        row_text = " ".join(line.text for line in row_lines)

        short_date_match = SHORT_DATE_RE.search(row_text)
        if short_date_match:
            month, day = (int(value) for value in short_date_match.groups())
            date_time = f"{year}-{month:02d}-{day:02d} {time_value}"
        else:
            date_time = time_value

        transaction_ids: list[tuple[float, float, str]] = []
        bet_values: list[tuple[float, float]] = []
        profit_values: list[tuple[float, float]] = []
        for word in row_words:
            for match in TRANSACTION_ID_RE.finditer(word.text):
                transaction_ids.append((word.y_center, word.x_center, match.group(1)))
            for value in extract_money(word.text):
                if word.x_center >= 0.72:
                    profit_values.append((word.y_center, value))
                elif word.x_center >= 0.40:
                    bet_values.append((word.y_center, value))

        transaction_ids.sort(key=lambda item: (item[0], item[1]))
        transaction_values: list[str] = []
        for _, _, transaction_id in transaction_ids:
            if transaction_id not in transaction_values:
                transaction_values.append(transaction_id)
        bet_values.sort(key=lambda item: item[0])
        profit_values.sort(key=lambda item: item[0])

        confidence = (
            sum(line.confidence for line in row_lines) / len(row_lines)
            if row_lines
            else 0.0
        )
        record = {
            "source_type": "alo789",
            "datetime": date_time,
            "game": "",
            "bet_amount": bet_values[0][1] if bet_values else None,
            "valid_bet": None,
            "net_result": profit_values[0][1] if profit_values else None,
            "ticket_id": " / ".join(transaction_values),
            "status": "",
            "needs_review": False,
            "ocr_confidence": round(confidence, 3),
        }
        record["needs_review"] = any(
            [
                not record["datetime"],
                not record["ticket_id"],
                record["bet_amount"] is None,
                record["net_result"] is None,
                confidence < 0.70,
            ]
        )
        records.append(record)

    return records


def parse_records(lines: list[OCRLine]) -> list[dict[str, Any]]:
    source_type = detect_source_type(lines)
    if source_type == "alo789":
        return parse_alo789_records(lines)
    return parse_sc8882_records(lines)


def run_ocr(image: Image.Image) -> tuple[list[OCRLine], list[dict[str, Any]]]:
    lines = parse_ocr_lines(image)
    return lines, parse_records(lines)


def open_database() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    connection = open_database()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_records (
                record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                saved_at TEXT NOT NULL,
                source_image TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'sc8882',
                datetime TEXT,
                game TEXT NOT NULL DEFAULT '',
                bet_amount REAL,
                valid_bet REAL,
                net_result REAL,
                ticket_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                needs_review INTEGER NOT NULL DEFAULT 0,
                ocr_confidence REAL
            )
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(saved_records)")
        }
        if "source_type" not in columns:
            connection.execute(
                "ALTER TABLE saved_records ADD COLUMN source_type TEXT NOT NULL DEFAULT 'sc8882'"
            )
        connection.commit()
    finally:
        connection.close()


def load_saved_records() -> list[dict[str, Any]]:
    connection = open_database()
    try:
        rows = connection.execute(
            "SELECT * FROM saved_records ORDER BY record_id"
        ).fetchall()
        records = [dict(row) for row in rows]
        for record in records:
            record.setdefault("source_type", "sc8882")
            record["needs_review"] = bool(record["needs_review"])
        return records
    finally:
        connection.close()


def duplicate_key(record: dict[str, Any]) -> tuple[Any, ...] | None:
    """Build a stable duplicate key, preferring the ticket ID from the source."""
    source_type = fold_text(str(record.get("source_type") or "sc8882")) or "sc8882"
    ticket_id = re.sub(r"\s+", "", str(record.get("ticket_id") or ""))
    if ticket_id:
        digits = re.sub(r"\D", "", ticket_id)
        return ("ticket_id", source_type, digits or ticket_id.casefold())

    # If OCR missed the ticket ID, only use a conservative fingerprint when
    # enough other fields are present to avoid collapsing incomplete rows.
    date_time = re.sub(r"\s+", " ", str(record.get("datetime") or "").strip())
    game = fold_text(str(record.get("game") or ""))
    status = fold_text(str(record.get("status") or ""))
    amounts = tuple(
        None if record.get(field) is None or pd.isna(record.get(field))
        else round(float(record[field]), 8)
        for field in ("bet_amount", "valid_bet", "net_result")
    )
    populated_fields = sum(
        [bool(game), amounts[0] is not None, amounts[1] is not None,
         amounts[2] is not None, bool(status)]
    )
    if date_time and populated_fields >= 3:
        return ("record", source_type, date_time, game, *amounts, status)
    return None


def save_ocr_batch(
    records: list[dict[str, Any]], batch_id: str, saved_at: str
) -> tuple[int, int]:
    connection = open_database()
    try:
        existing_rows = connection.execute(
            """
            SELECT source_type, datetime, game, bet_amount, valid_bet, net_result, ticket_id, status
            FROM saved_records
            """
        ).fetchall()
        known_keys = {
            key
            for row in existing_rows
            if (key := duplicate_key(dict(row))) is not None
        }

        new_records: list[dict[str, Any]] = []
        skipped_duplicates = 0
        for record in records:
            key = duplicate_key(record)
            if key is not None and key in known_keys:
                skipped_duplicates += 1
                continue
            if key is not None:
                known_keys.add(key)
            new_records.append(record)

        with connection:
            if new_records:
                connection.executemany(
                    """
                    INSERT INTO saved_records (
                        batch_id, saved_at, source_image, source_type, datetime, game,
                        bet_amount, valid_bet, net_result, ticket_id, status,
                        needs_review, ocr_confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch_id,
                            saved_at,
                            record.get("source_image", ""),
                            record.get("source_type", "sc8882"),
                            record.get("datetime"),
                            record.get("game", ""),
                            record.get("bet_amount"),
                            record.get("valid_bet"),
                            record.get("net_result"),
                            record.get("ticket_id", ""),
                            record.get("status", ""),
                            int(bool(record.get("needs_review", False))),
                            record.get("ocr_confidence"),
                        )
                        for record in new_records
                    ],
                )
        return len(new_records), skipped_duplicates
    finally:
        connection.close()


def save_edited_records(frame: pd.DataFrame) -> None:
    now = DateTime.now().astimezone().isoformat(timespec="seconds")
    connection = open_database()
    try:
        with connection:
            original_ids = {
                int(row[0])
                for row in connection.execute("SELECT record_id FROM saved_records")
            }
            kept_ids: set[int] = set()
            for row in frame.to_dict("records"):
                raw_id = row.get("record_id")
                record_id = None if pd.isna(raw_id) else int(raw_id)

                def nullable_text(field: str, default: str = "") -> str:
                    value = row.get(field)
                    if value is None or pd.isna(value):
                        return default
                    return str(value)

                def nullable_number(field: str) -> float | None:
                    value = row.get(field)
                    if value is None or pd.isna(value):
                        return None
                    return float(value)

                values = (
                    nullable_text("batch_id", "manual") or "manual",
                    nullable_text("saved_at", now) or now,
                    nullable_text("source_image", "Nhập thủ công") or "Nhập thủ công",
                    nullable_text("source_type", "sc8882") or "sc8882",
                    nullable_text("datetime") or None,
                    nullable_text("game"),
                    nullable_number("bet_amount"),
                    nullable_number("valid_bet"),
                    nullable_number("net_result"),
                    nullable_text("ticket_id"),
                    nullable_text("status"),
                    int(bool(row.get("needs_review", False)))
                    if not pd.isna(row.get("needs_review", False))
                    else 0,
                    nullable_number("ocr_confidence"),
                )

                if record_id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO saved_records (
                            batch_id, saved_at, source_image, source_type, datetime, game,
                            bet_amount, valid_bet, net_result, ticket_id, status,
                            needs_review, ocr_confidence
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
                    record_id = int(cursor.lastrowid)
                else:
                    connection.execute(
                        """
                        UPDATE saved_records SET
                            batch_id=?, saved_at=?, source_image=?, source_type=?, datetime=?, game=?,
                            bet_amount=?, valid_bet=?, net_result=?, ticket_id=?, status=?,
                            needs_review=?, ocr_confidence=?
                        WHERE record_id=?
                        """,
                        (*values, record_id),
                    )
                kept_ids.add(record_id)

            removed_ids = original_ids - kept_ids
            if removed_ids:
                connection.executemany(
                    "DELETE FROM saved_records WHERE record_id=?",
                    [(record_id,) for record_id in removed_ids],
                )
    finally:
        connection.close()


def delete_saved_records(record_ids: list[int]) -> int:
    unique_ids = sorted({int(record_id) for record_id in record_ids})
    if not unique_ids:
        return 0

    connection = open_database()
    try:
        with connection:
            before = connection.total_changes
            connection.executemany(
                "DELETE FROM saved_records WHERE record_id=?",
                [(record_id,) for record_id in unique_ids],
            )
            return connection.total_changes - before
    finally:
        connection.close()


def delete_all_saved_records() -> int:
    connection = open_database()
    try:
        with connection:
            before = connection.total_changes
            connection.execute("DELETE FROM saved_records")
            return connection.total_changes - before
    finally:
        connection.close()


initialize_database()

st.set_page_config(page_title="Đọc lượt từ ảnh", layout="wide")
st.title("Đọc danh sách lượt từ ảnh")
st.caption(
    "Chọn một ảnh dài hoặc nhiều ảnh. Mỗi lần đọc sẽ được thêm vào danh sách đã lưu "
    "trên máy; lượt trùng sẽ được bỏ qua khi lưu. Xóa ảnh khỏi vùng tải lên không "
    "xóa các lượt đã đọc."
)

if "upload_widget_version" not in st.session_state:
    st.session_state["upload_widget_version"] = 0
if "ocr_lines_by_image" not in st.session_state:
    st.session_state["ocr_lines_by_image"] = []
if "ocr_errors" not in st.session_state:
    st.session_state["ocr_errors"] = []
if "ledger_revision" not in st.session_state:
    st.session_state["ledger_revision"] = 0

uploaded_files = st.file_uploader(
    "Chọn một hoặc nhiều ảnh cần thêm vào danh sách",
    type=["png", "jpg", "jpeg", "webp", "bmp"],
    accept_multiple_files=True,
    key=f"uploaded_images_{st.session_state['upload_widget_version']}",
)

if uploaded_files:
    st.write(f"Đã chọn **{len(uploaded_files)} ảnh**. Kết quả sẽ nối thêm vào danh sách đã lưu.")
    with st.expander("Xem tên các ảnh đang chọn"):
        for index, uploaded_file in enumerate(uploaded_files, start=1):
            st.write(f"{index}. {uploaded_file.name}")

    clear_column, process_column = st.columns([1, 3])
    with clear_column:
        if st.button("Bỏ ảnh đang chọn", help="Chỉ bỏ ảnh khỏi vùng tải lên; không xóa lượt đã lưu."):
            st.session_state["upload_widget_version"] += 1
            st.rerun()
    with process_column:
        if st.button("Đọc và lưu vào danh sách", type="primary"):
            batch_id = uuid4().hex
            saved_at = DateTime.now().astimezone().isoformat(timespec="seconds")
            combined_records: list[dict[str, Any]] = []
            combined_ocr_lines: list[dict[str, Any]] = []
            errors: list[str] = []
            progress = st.progress(0, text="Đang chuẩn bị OCR…")

            for index, uploaded_file in enumerate(uploaded_files, start=1):
                source_image = f"{index}. {uploaded_file.name}"
                try:
                    image_bytes = uploaded_file.getvalue()
                    image = ImageOps.exif_transpose(
                        Image.open(io.BytesIO(image_bytes))
                    ).convert("RGB")
                    with st.spinner(
                        f"Đang đọc ảnh {index}/{len(uploaded_files)}: {uploaded_file.name}"
                    ):
                        ocr_lines, image_records = run_ocr(image)

                    combined_ocr_lines.append(
                        {"source_image": source_image, "lines": ocr_lines}
                    )
                    combined_records.extend(
                        {"source_image": source_image, **record}
                        for record in image_records
                    )
                    if not image_records:
                        errors.append(
                            f"{source_image}: chưa ghép được lượt từ nội dung OCR."
                        )
                except pytesseract.TesseractNotFoundError:
                    errors.append(
                        f"{source_image}: không tìm thấy Tesseract. Hãy cài Tesseract "
                        "và thêm vào PATH, hoặc chạy lại Docker sau khi build image."
                    )
                except pytesseract.TesseractError as error:
                    errors.append(
                        f"{source_image}: lỗi OCR ({error}); hãy kiểm tra gói ngôn ngữ vie."
                    )
                except Exception as error:
                    errors.append(f"{source_image}: không đọc được ảnh ({error}).")
                finally:
                    progress.progress(
                        index / len(uploaded_files),
                        text=f"Đã xử lý {index}/{len(uploaded_files)} ảnh",
                    )

            st.session_state["ocr_lines_by_image"] = combined_ocr_lines
            st.session_state["ocr_errors"] = errors
            if combined_records:
                saved_count, duplicate_count = save_ocr_batch(
                    combined_records, batch_id, saved_at
                )
                if saved_count:
                    st.session_state["ledger_revision"] += 1
                if duplicate_count:
                    st.info(f"Đã bỏ qua {duplicate_count} lượt bị trùng.")
                if saved_count:
                    st.success(
                        f"Đã lưu {saved_count} lượt mới từ {len(uploaded_files)} ảnh."
                    )
                elif duplicate_count:
                    st.warning("Tất cả lượt đọc được đều đã có trong danh sách; không lưu thêm.")
                else:
                    st.warning("Chưa nhận diện được lượt mới để lưu.")
            else:
                st.warning("Chưa nhận diện được lượt nào; không có dữ liệu mới được lưu.")

records = load_saved_records()
ocr_lines_by_image = st.session_state["ocr_lines_by_image"]
ocr_errors = st.session_state["ocr_errors"]

delete_notice = st.session_state.pop("delete_notice", None)
if delete_notice:
    st.success(delete_notice)

for error in ocr_errors:
    st.warning(error)

st.subheader(f"Danh sách đã lưu ({len(records)} lượt)")
st.caption(f"Dữ liệu được lưu cục bộ tại `{DATABASE_PATH}`. Ảnh gốc không được lưu.")

if records:
    if "confirm_delete_all" not in st.session_state:
        st.session_state["confirm_delete_all"] = False

    if not st.session_state["confirm_delete_all"]:
        if st.button("Xóa hết danh sách đã lưu", key="request_delete_all"):
            st.session_state["confirm_delete_all"] = True
            st.rerun()
    else:
        st.warning(
            f"Bạn sắp xóa toàn bộ {len(records)} lượt đã lưu. Thao tác này không thể hoàn tác."
        )
        confirm_column, cancel_column = st.columns([1, 1])
        with confirm_column:
            if st.button(
                "Xác nhận xóa hết",
                key="confirm_delete_all_button",
                type="primary",
            ):
                deleted_count = delete_all_saved_records()
                st.session_state["ledger_revision"] += 1
                st.session_state["confirm_delete_all"] = False
                st.session_state["delete_notice"] = (
                    f"Đã xóa toàn bộ {deleted_count} lượt đã lưu."
                )
                st.rerun()
        with cancel_column:
            if st.button("Hủy", key="cancel_delete_all"):
                st.session_state["confirm_delete_all"] = False
                st.rerun()

    frame = pd.DataFrame(records)
    frame.insert(0, "delete_selected", False)
    editor_key = f"saved_records_{st.session_state['ledger_revision']}"
    st.caption(
        "Đánh dấu các dòng muốn xóa rồi bấm nút bên dưới. Nút xóa áp dụng ngay; "
        "hãy lưu các chỉnh sửa khác trước nếu muốn giữ chúng."
    )
    edited = st.data_editor(
        frame,
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
        column_config={
            "delete_selected": st.column_config.CheckboxColumn("Chọn xóa"),
            "record_id": None,
            "batch_id": None,
            "saved_at": st.column_config.TextColumn("Lưu lúc", disabled=True),
            "source_image": st.column_config.TextColumn("Ảnh nguồn", disabled=True),
            "source_type": st.column_config.TextColumn("Phân loại", disabled=True),
            "datetime": st.column_config.TextColumn("Thời gian"),
            "game": st.column_config.TextColumn("Trò chơi"),
            "bet_amount": st.column_config.NumberColumn("Tiền cược", format="%.2f"),
            "valid_bet": st.column_config.NumberColumn("Cược hợp lệ", format="%.2f"),
            "net_result": st.column_config.NumberColumn("Thắng/Thua", format="%.2f"),
            "ticket_id": st.column_config.TextColumn("ID vé"),
            "status": st.column_config.TextColumn("Trạng thái"),
            "ocr_confidence": st.column_config.NumberColumn(
                "Độ tin cậy", format="%.3f", disabled=True
            ),
            "needs_review": st.column_config.CheckboxColumn("Cần kiểm tra?"),
        },
        key=editor_key,
    )

    action_delete, action_save = st.columns([1, 2])
    selected_for_deletion = edited["delete_selected"].fillna(False).astype(bool)
    selected_ids = (
        pd.to_numeric(
            edited.loc[selected_for_deletion, "record_id"], errors="coerce"
        )
        .dropna()
        .astype(int)
        .tolist()
    )
    with action_delete:
        if st.button(
            "Xóa dòng đã chọn",
            key=f"delete_records_{editor_key}",
            type="secondary",
            disabled=not selected_ids,
        ):
            deleted_count = delete_saved_records(selected_ids)
            st.session_state["ledger_revision"] += 1
            st.session_state["delete_notice"] = f"Đã xóa {deleted_count} dòng đã chọn."
            st.rerun()
    with action_save:
        if st.button(
            "Lưu sửa đổi/xóa dòng trong bảng",
            key=f"save_records_{editor_key}",
        ):
            save_edited_records(edited.drop(columns=["delete_selected"], errors="ignore"))
            st.success("Đã lưu các chỉnh sửa vào danh sách trên máy.")

    total_bet = pd.to_numeric(edited["bet_amount"], errors="coerce").sum()
    total_result = pd.to_numeric(edited["net_result"], errors="coerce").sum()
    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("Số lượt", len(edited))
    metric2.metric("Tổng cược", f"{total_bet:.2f}")
    metric3.metric("Tổng thắng/thua", f"{total_result:.2f}")

    ticket_ids = edited["ticket_id"].astype(str).str.strip()
    duplicate_ids = ticket_ids.ne("") & ticket_ids.duplicated(keep=False)
    if duplicate_ids.any():
        st.warning(
            f"Danh sách hiện có {int(duplicate_ids.sum())} dòng trùng ID vé từ dữ liệu cũ "
            "hoặc chỉnh sửa thủ công. Các lượt trùng trong lần đọc mới sẽ được tự bỏ qua."
        )

    export_columns = [
        column
        for column in edited.columns
        if column not in {"record_id", "batch_id", "delete_selected"}
    ]
    csv_bytes = edited[export_columns].to_csv(
        index=False,
        sep=";",
        decimal=",",
    ).encode("utf-8-sig")
    st.download_button(
        "Tải CSV (mỗi lượt một hàng, dễ mở bằng Excel)",
        data=csv_bytes,
        file_name="danh_sach_luot.csv",
        mime="text/csv",
    )
    st.caption(
        "CSV dùng dấu chấm phẩy để ngăn cách cột và dấu phẩy cho phần thập phân. Bảng chứa lượt "
        "từ các lần đọc trước. Sửa hoặc xóa dòng rồi bấm **Lưu sửa đổi/xóa dòng** để giữ thay đổi. "
        "Khi chỉ xóa ảnh đang chọn, các lượt đã lưu vẫn còn."
    )
else:
    st.info("Chưa có lượt nào được lưu. Chọn ảnh phía trên rồi bấm **Đọc và lưu vào danh sách**.")

for image_result in ocr_lines_by_image:
    lines = image_result["lines"]
    with st.expander(f"OCR thô — {image_result['source_image']} ({len(lines)} dòng)"):
        st.code(
            "\n".join(f"{line.confidence:0.0%}  {line.text}" for line in lines),
            language="text",
        )
