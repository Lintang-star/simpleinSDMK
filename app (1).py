# -*- coding: utf-8 -*-
"""
SIMKERMA-SDMK
Dashboard Pemantauan MoU, PKS, Kesepakatan Bersama (KB),
Praktik Mahasiswa dan Penelitian - untuk PJ SDMK.

Jalankan lokal :  streamlit run app.py
Deploy         :  GitHub -> Streamlit Community Cloud (main file: app.py)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import inspect
import io
import json
import math
import os
import random
import secrets as pysecrets
import sqlite3
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="SIMKERMA SDMK",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_VERSION = "1.0.0"

# =============================================================================
# 1. UTILITAS UMUM
# =============================================================================
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
         "Agustus", "September", "Oktober", "November", "Desember"]


def now_wib() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=7)


def today_wib() -> date:
    return now_wib().date()


TODAY = today_wib()


def _wide(fn) -> dict:
    """Kompatibel lintas versi Streamlit (use_container_width / width='stretch')."""
    try:
        params = inspect.signature(fn).parameters
        if "use_container_width" in params:
            return {"use_container_width": True}
        if "width" in params:
            return {"width": "stretch"}
    except Exception:
        pass
    return {}


WB = _wide(st.button)
WD = _wide(st.dataframe)
WP = _wide(st.plotly_chart)


def get_secret(key: str, default=None):
    try:
        val = st.secrets[key]
        return default if val is None else val
    except Exception:
        return default


def is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def clean(v):
    if v is None or is_nan(v):
        return None
    try:
        if v is pd.NaT or pd.isna(v):
            return None
    except Exception:
        pass
    return v


def truthy(v) -> bool:
    v = clean(v)
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "ya", "y", "yes", "v", "✔")
    try:
        return bool(int(v))
    except Exception:
        return bool(v)


def parse_date(v):
    v = clean(v)
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def fmt_date(v) -> str:
    d = parse_date(v)
    return d.strftime("%d/%m/%Y") if d else "-"


def fmt_dates(series: pd.Series) -> pd.Series:
    d = pd.to_datetime(series, errors="coerce")
    return d.dt.strftime("%d/%m/%Y").fillna("-")


def rupiah(v) -> str:
    try:
        n = float(clean(v) or 0)
    except Exception:
        n = 0.0
    return "Rp " + f"{n:,.0f}".replace(",", ".")


def days_left(series: pd.Series) -> pd.Series:
    end = pd.to_datetime(series, errors="coerce")
    d = (end - pd.Timestamp(TODAY)).dt.days
    return d.astype("Int64")


def long_date(d: date) -> str:
    return f"{HARI[d.weekday()]}, {d.day} {BULAN[d.month - 1]} {d.year}"


def norm(s) -> str:
    return str(s or "").replace("*", "").strip().lower()


# =============================================================================
# 2. KONFIGURASI: PALET WARNA, DEFAULT PENGATURAN, MASTER DATA
# =============================================================================
PALETTES = {
    "Biru Samudra & Emas": dict(primary="#0B3C5D", secondary="#1F6F9F", accent="#C9A227",
                                soft="#EAF1F7", bg="#F5F7FA", text="#1B2733"),
    "Hijau Zamrud & Emas": dict(primary="#0F5132", secondary="#2E7D5B", accent="#B8892B",
                                soft="#E7F2EC", bg="#F6F9F7", text="#1B2B23"),
    "Marun Formal": dict(primary="#6B1F2A", secondary="#9C3B49", accent="#C8A24A",
                         soft="#F6EAEC", bg="#FAF7F7", text="#2B1C1F"),
    "Teal Modern": dict(primary="#0E4D64", secondary="#168AAD", accent="#F2A541",
                        soft="#E4F2F6", bg="#F5F9FA", text="#162A31"),
    "Ungu Slate": dict(primary="#3B2F63", secondary="#5B4B9A", accent="#D9A520",
                       soft="#EFEBF8", bg="#F8F7FB", text="#221C38"),
}
OK_C, WARN_C, BAD_C = "#2E8B57", "#E0A100", "#C0392B"

DEFAULTS = {
    "app_name": "SIMKERMA SDMK",
    "instansi": "Dinas Kesehatan",
    "unit": "Bidang / Sub Bagian SDMK",
    "default_pj": "",
    "palette": "Biru Samudra & Emas",
    "warn_days": 90,
    "kritis_days": 30,
    "horizon_days": 60,
    "max_upload_mb": 5,
    "auto_backup": False,
    "backup_interval_min": 30,
}

DEFAULT_MASTER = {
    "jenis_dokumen": ["MoU", "PKS", "KB"],
    "jenis_mitra": ["Perguruan Tinggi", "Rumah Sakit", "Puskesmas", "Klinik/Praktik Mandiri",
                    "Dinas Kesehatan/Pemda", "Organisasi Profesi", "Lembaga Swasta/Industri",
                    "Lembaga Pemerintah Lain", "LSM/Yayasan", "Lainnya"],
    "bidang": ["Pendidikan & Pelatihan", "Praktik Klinik/Lapangan", "Penelitian & Pengembangan",
               "Pengabdian Masyarakat", "Pemanfaatan/Penempatan SDMK", "Pertukaran Data/Informasi",
               "Pelayanan Kesehatan", "Lainnya"],
    "status_dokumen": ["Aktif", "Draft/Proses", "Diperpanjang", "Dihentikan"],
    "jenjang": ["SMK Kesehatan", "D3", "D4/Sarjana Terapan", "S1", "Profesi/Ners", "Spesialis", "S2", "S3"],
    "status_praktik": ["Pengajuan", "Verifikasi Berkas", "Disetujui", "Berlangsung",
                       "Selesai", "Ditolak", "Dibatalkan"],
    "jenis_penelitian": ["Skripsi", "Tesis", "Disertasi", "Penelitian Dosen",
                         "Penelitian Institusi/Lembaga", "Survei/Evaluasi Program", "Lainnya"],
    "status_penelitian": ["Pengajuan", "Verifikasi Berkas", "Izin Terbit", "Berlangsung",
                          "Laporan Diserahkan", "Selesai", "Ditolak", "Dibatalkan"],
    "modul_tugas": ["Umum", "Dokumen Kerja Sama", "Praktik Mahasiswa", "Penelitian"],
    "prioritas": ["Rendah", "Sedang", "Tinggi", "Mendesak"],
    "status_tugas": ["Belum Mulai", "Berjalan", "Selesai", "Ditunda"],
}
MASTER_LABELS = {
    "jenis_dokumen": "Jenis Dokumen (disarankan jangan diubah)",
    "jenis_mitra": "Jenis Mitra",
    "bidang": "Bidang Kerja Sama",
    "status_dokumen": "Status Dokumen",
    "jenjang": "Jenjang Pendidikan",
    "status_praktik": "Status Praktik Mahasiswa",
    "jenis_penelitian": "Jenis Penelitian",
    "status_penelitian": "Status Penelitian",
    "modul_tugas": "Modul Tindak Lanjut",
    "prioritas": "Prioritas Tugas",
    "status_tugas": "Status Tugas",
}
DONE_PRAKTIK = {"Selesai"}
CLOSED = {"Ditolak", "Dibatalkan"}
DONE_RISET = {"Selesai", "Laporan Diserahkan"}
EARLY_RISET = {"Pengajuan", "Verifikasi Berkas"}

MASA_COLOR = {
    "Berlaku": OK_C, "Berlaku (tanpa batas)": "#1F6F9F", "Segera berakhir": WARN_C,
    "Kritis": "#E4572E", "Kadaluarsa": BAD_C, "Dalam proses": "#7F8C9A",
    "Dihentikan": "#4A4A4A", "Belum mulai": "#5DADE2", "Tanpa tanggal": "#BDC3C7",
}
EMO = {
    "Berlaku": "🟢", "Berlaku (tanpa batas)": "🟢", "Segera berakhir": "🟠", "Kritis": "🔴",
    "Kadaluarsa": "⛔", "Dalam proses": "🟡", "Dihentikan": "⚫", "Belum mulai": "🔵",
    "Tanpa tanggal": "⚪", "Sedang berjalan": "🟢", "Akan datang": "🔵", "Lewat jadwal": "🔴",
    "Selesai": "✅", "Ditolak": "⛔", "Dibatalkan": "⚫", "Terlambat": "🔴",
    "Jatuh tempo ≤7 hari": "🟠", "Terjadwal": "🔵", "-": "⚪",
}


def badge(label) -> str:
    label = str(clean(label) or "-")
    return f"{EMO.get(label, '⚪')} {label}"


# =============================================================================
# 3. SKEMA DATA (satu definisi -> tabel, form, validasi, impor, ekspor)
# =============================================================================
def F(key, label, ftype="text", **kw):
    d = {"key": key, "label": label, "type": ftype}
    d.update(kw)
    return d


TABLES = {
    "dokumen": {
        "title": "MoU · PKS · Kesepakatan Bersama",
        "singular": "Dokumen Kerja Sama",
        "fields": [
            F("jenis", "Jenis Dokumen", "select", opts="jenis_dokumen", req=True, show=True,
              short="Jenis", strict=True, help="MoU = Memorandum of Understanding · PKS = Perjanjian Kerja Sama · KB = Kesepakatan Bersama"),
            F("nomor", "Nomor Dokumen (Pihak Kesatu)", "text", req=True, show=True, short="Nomor"),
            F("nomor_mitra", "Nomor Dokumen (Pihak Kedua/Mitra)", "text", short="No. Mitra"),
            F("mitra", "Nama Mitra / Instansi", "text", req=True, show=True, short="Mitra"),
            F("jenis_mitra", "Jenis Mitra", "select", opts="jenis_mitra", show=True, short="Jenis Mitra"),
            F("bidang", "Bidang Kerja Sama", "select", opts="bidang", show=True, short="Bidang"),
            F("induk", "Turunan dari (MoU/KB induk)", "ref_dokumen", short="Induk",
              help="Isi bila PKS ini merupakan turunan dari MoU/KB tertentu."),
            F("tgl_ttd", "Tanggal Penandatanganan", "date", short="TTD"),
            F("tgl_mulai", "Tanggal Mulai Berlaku", "date", req=True, show=True, short="Mulai"),
            F("tgl_berakhir", "Tanggal Berakhir", "date", show=True, short="Berakhir"),
            F("tanpa_batas", "Berlaku tanpa batas waktu", "bool", short="Tanpa Batas"),
            F("status", "Status Dokumen", "select", opts="status_dokumen", req=True, show=True, short="Status"),
            F("pj", "Penanggung Jawab (PJ)", "text", show=True, short="PJ"),
            F("kontak_pj", "Kontak PJ (HP/Email)", "text", short="Kontak PJ"),
            F("kontak_mitra", "Kontak Mitra", "text", short="Kontak Mitra"),
            F("link", "Tautan Dokumen (Google Drive/URL)", "url", show=True, short="Link"),
            F("judul", "Judul / Tentang", "text", req=True, full=True, short="Judul"),
            F("ruang_lingkup", "Ruang Lingkup Kerja Sama", "textarea", full=True, short="Ruang Lingkup"),
            F("catatan", "Catatan", "textarea", full=True, short="Catatan"),
        ],
        "filters": [("jenis", "Jenis"), ("masa_berlaku", "Masa Berlaku"), ("jenis_mitra", "Jenis Mitra")],
        "new_defaults": {"status": "Aktif"},
        "date_cols": ["tgl_ttd", "tgl_mulai", "tgl_berakhir"],
    },
    "praktik": {
        "title": "Praktik Mahasiswa",
        "singular": "Praktik Mahasiswa",
        "fields": [
            F("institusi", "Institusi Pendidikan", "text", req=True, show=True, short="Institusi"),
            F("prodi", "Program Studi", "text", req=True, show=True, short="Prodi"),
            F("jenjang", "Jenjang", "select", opts="jenjang", req=True, show=True, short="Jenjang"),
            F("jumlah", "Jumlah Mahasiswa", "int", req=True, min=1, show=True, short="Jml Mhs"),
            F("lokasi", "Lokasi Praktik (Unit/Fasyankes)", "text", req=True, show=True, short="Lokasi"),
            F("tgl_mulai", "Tanggal Mulai", "date", req=True, show=True, short="Mulai"),
            F("tgl_selesai", "Tanggal Selesai", "date", req=True, show=True, short="Selesai"),
            F("pembimbing_inst", "Pembimbing Institusi", "text", short="Pembimbing Inst."),
            F("pembimbing_lahan", "Pembimbing Lahan", "text", short="Pembimbing Lahan"),
            F("no_surat", "Nomor Surat Permohonan/Izin", "text", show=True, short="No. Surat"),
            F("dokumen_ref", "Dasar Kerja Sama (PKS/MoU/KB)", "ref_dokumen", short="Dasar KS"),
            F("status", "Status", "select", opts="status_praktik", req=True, show=True, short="Status"),
            F("berkas_lengkap", "Berkas persyaratan lengkap", "bool", short="Berkas Lengkap"),
            F("evaluasi_diterima", "Laporan/evaluasi praktik sudah diterima", "bool", short="Evaluasi"),
            F("retribusi", "Retribusi / Biaya (Rp)", "number", show=True, short="Retribusi"),
            F("pj", "Penanggung Jawab (PJ)", "text", show=True, short="PJ"),
            F("kontak", "Kontak Institusi (HP/Email)", "text", short="Kontak"),
            F("link", "Tautan Berkas (Google Drive/URL)", "url", short="Link"),
            F("catatan", "Catatan", "textarea", full=True, short="Catatan"),
        ],
        "filters": [("status", "Status"), ("waktu", "Status Waktu"), ("jenjang", "Jenjang")],
        "new_defaults": {},
        "date_cols": ["tgl_mulai", "tgl_selesai"],
    },
    "penelitian": {
        "title": "Penelitian",
        "singular": "Penelitian",
        "fields": [
            F("peneliti", "Nama Peneliti", "text", req=True, show=True, short="Peneliti"),
            F("institusi", "Institusi Asal", "text", req=True, show=True, short="Institusi"),
            F("jenis", "Jenis Penelitian", "select", opts="jenis_penelitian", req=True, show=True, short="Jenis"),
            F("lokasi", "Lokasi Penelitian", "text", show=True, short="Lokasi"),
            F("no_surat", "No. Surat Rekomendasi / Izin Penelitian", "text", show=True, short="No. Izin"),
            F("no_etik", "No. Kelaikan Etik (Ethical Clearance)", "text", short="No. Etik"),
            F("tgl_pengajuan", "Tanggal Pengajuan", "date", req=True, short="Diajukan"),
            F("tgl_mulai", "Tanggal Mulai", "date", req=True, show=True, short="Mulai"),
            F("tgl_selesai", "Tanggal Selesai", "date", req=True, show=True, short="Selesai"),
            F("status", "Status", "select", opts="status_penelitian", req=True, show=True, short="Status"),
            F("laporan_diserahkan", "Laporan hasil sudah diserahkan", "bool", show=True, short="Laporan"),
            F("tgl_laporan", "Tanggal Laporan Diserahkan", "date", short="Tgl Laporan"),
            F("pj", "Penanggung Jawab (PJ)", "text", show=True, short="PJ"),
            F("kontak", "Kontak Peneliti (HP/Email)", "text", short="Kontak"),
            F("link", "Tautan Berkas (Google Drive/URL)", "url", short="Link"),
            F("judul", "Judul Penelitian", "text", req=True, full=True, short="Judul"),
            F("catatan", "Catatan", "textarea", full=True, short="Catatan"),
        ],
        "filters": [("status", "Status"), ("waktu", "Status Waktu"), ("jenis", "Jenis")],
        "new_defaults": {},
        "date_cols": ["tgl_pengajuan", "tgl_mulai", "tgl_selesai", "tgl_laporan"],
    },
    "tugas": {
        "title": "Tindak Lanjut & Tugas",
        "singular": "Tugas",
        "fields": [
            F("modul", "Modul Terkait", "select", opts="modul_tugas", req=True, show=True, short="Modul"),
            F("referensi", "Referensi (nomor/nama terkait)", "text", show=True, short="Referensi"),
            F("jatuh_tempo", "Jatuh Tempo", "date", req=True, show=True, short="Jatuh Tempo"),
            F("prioritas", "Prioritas", "select", opts="prioritas", req=True, show=True, short="Prioritas"),
            F("status", "Status", "select", opts="status_tugas", req=True, show=True, short="Status"),
            F("pj", "Penanggung Jawab", "text", show=True, short="PJ"),
            F("judul", "Judul Tugas / Tindak Lanjut", "text", req=True, full=True, short="Tugas"),
            F("deskripsi", "Deskripsi", "textarea", full=True, short="Deskripsi"),
            F("catatan", "Catatan", "textarea", full=True, short="Catatan"),
        ],
        "filters": [("status", "Status"), ("waktu", "Status Waktu"), ("prioritas", "Prioritas")],
        "new_defaults": {"prioritas": "Sedang"},
        "date_cols": ["jatuh_tempo"],
    },
}
COMPUTED_SHOW = {
    "dokumen": [("masa_berlaku", "Masa Berlaku"), ("sisa_hari", "Sisa Hari")],
    "praktik": [("waktu", "Status Waktu"), ("sisa_hari", "Sisa Hari")],
    "penelitian": [("waktu", "Status Waktu"), ("sisa_hari", "Sisa Hari")],
    "tugas": [("waktu", "Status Waktu"), ("sisa_hari", "Sisa Hari")],
}


def record_label(table: str, r: dict) -> str:
    rid = int(r.get("id", 0))
    g = lambda k: str(clean(r.get(k)) or "-")
    if table == "dokumen":
        return f"#{rid} · {g('jenis')} · {g('nomor')} · {g('mitra')}"
    if table == "praktik":
        return f"#{rid} · {g('institusi')} · {g('prodi')} · {fmt_date(r.get('tgl_mulai'))}"
    if table == "penelitian":
        return f"#{rid} · {g('peneliti')} — {g('judul')[:60]}"
    return f"#{rid} · {g('judul')[:70]} · {g('status')}"


# =============================================================================
# 4. DATABASE (SQLite) + BACKUP
# =============================================================================
LOCK = threading.RLock()


def resolve_db_path() -> str:
    p = os.environ.get("SDMK_DB_PATH") or get_secret("DB_PATH") or "data/kerjasama_sdmk.db"
    folder = os.path.dirname(p) or "."
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write_test")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        return p
    except Exception:
        return os.path.join(tempfile.gettempdir(), "kerjasama_sdmk.db")


@st.cache_resource(show_spinner=False)
def get_conn(path: str):
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    return conn


@st.cache_resource(show_spinner=False)
def get_state() -> dict:
    return {"dirty": False, "last_backup": 0.0, "boot_msg": None}


@st.cache_resource(show_spinner=False)
def get_db_path() -> str:
    return resolve_db_path()


DB_PATH = get_db_path()
CONN = get_conn(DB_PATH)
STATE = get_state()


def sql_type(t: str) -> str:
    return {"number": "REAL", "int": "INTEGER", "bool": "INTEGER"}.get(t, "TEXT")


def init_db():
    with LOCK:
        c = CONN
        for table, cfg in TABLES.items():
            cols = ", ".join(f"{f['key']} {sql_type(f['type'])}" for f in cfg["fields"])
            c.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, {cols}, dibuat TEXT, diubah TEXT)")
            existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
            for f in cfg["fields"]:
                if f["key"] not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {f['key']} {sql_type(f['type'])}")
        c.execute("CREATE TABLE IF NOT EXISTS lampiran (id INTEGER PRIMARY KEY AUTOINCREMENT, modul TEXT, ref_id INTEGER, nama TEXT, tipe TEXT, ukuran INTEGER, data BLOB, dibuat TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS aktivitas (id INTEGER PRIMARY KEY AUTOINCREMENT, waktu TEXT, pengguna TEXT, aksi TEXT, modul TEXT, ref TEXT, detail TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS pengaturan (kunci TEXT PRIMARY KEY, nilai TEXT)")
        c.commit()


init_db()


def q(sql: str, params=()) -> pd.DataFrame:
    with LOCK:
        return pd.read_sql_query(sql, CONN, params=params)


def ex(sql: str, params=(), track: bool = True):
    with LOCK:
        cur = CONN.execute(sql, params)
        CONN.commit()
        if track:
            STATE["dirty"] = True
        return cur.lastrowid


def now_str() -> str:
    return now_wib().strftime("%Y-%m-%d %H:%M:%S")


def current_user() -> str:
    return st.session_state.get("role", "admin")


def log_event(aksi: str, modul: str, ref: str = "", detail: str = ""):
    try:
        ex("INSERT INTO aktivitas (waktu, pengguna, aksi, modul, ref, detail) VALUES (?,?,?,?,?,?)",
           (now_str(), current_user(), aksi, modul, ref, detail[:500]), track=False)
    except Exception:
        pass


def load(table: str) -> pd.DataFrame:
    return q(f"SELECT * FROM {table} ORDER BY id DESC")


def fetch_one(table: str, rid: int) -> dict:
    with LOCK:
        cur = CONN.execute(f"SELECT * FROM {table} WHERE id=?", (int(rid),))
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
    return dict(zip(cols, row)) if row else {}


def to_db(f: dict, v):
    t = f["type"]
    if t == "date":
        d = parse_date(v)
        return d.isoformat() if d else None
    if t == "bool":
        return 1 if truthy(v) else 0
    if t == "int":
        try:
            return int(float(clean(v) or 0))
        except Exception:
            return 0
    if t == "number":
        try:
            return float(clean(v) or 0)
        except Exception:
            return 0.0
    return str(clean(v) or "").strip()


def save_record(table: str, data: dict, rid: int | None = None) -> int:
    fields = TABLES[table]["fields"]
    cols = [f["key"] for f in fields]
    vals = [to_db(f, data.get(f["key"])) for f in fields]
    if rid is None:
        sql = f"INSERT INTO {table} ({','.join(cols)},dibuat,diubah) VALUES ({','.join('?' * (len(cols) + 2))})"
        new_id = ex(sql, tuple(vals) + (now_str(), now_str()))
        log_event("Tambah", table, str(new_id), record_label(table, {"id": new_id, **data}))
        return int(new_id)
    sets = ",".join(f"{c}=?" for c in cols)
    ex(f"UPDATE {table} SET {sets}, diubah=? WHERE id=?", tuple(vals) + (now_str(), int(rid)))
    log_event("Ubah", table, str(rid), record_label(table, {"id": rid, **data}))
    return int(rid)


def delete_record(table: str, rid: int):
    label = record_label(table, fetch_one(table, rid) or {"id": rid})
    ex("DELETE FROM lampiran WHERE modul=? AND ref_id=?", (table, int(rid)))
    ex(f"DELETE FROM {table} WHERE id=?", (int(rid),))
    log_event("Hapus", table, str(rid), label)


# ---------- pengaturan ----------
def load_settings():
    d = dict(DEFAULTS)
    m = json.loads(json.dumps(DEFAULT_MASTER))
    try:
        df = q("SELECT kunci, nilai FROM pengaturan")
        for k, v in zip(df["kunci"], df["nilai"]):
            try:
                val = json.loads(v)
            except Exception:
                continue
            if k == "master" and isinstance(val, dict):
                for mk, mv in val.items():
                    if mk in m and isinstance(mv, list) and mv:
                        m[mk] = [str(x) for x in mv]
            else:
                d[k] = val
    except Exception:
        pass
    return d, m


def set_setting(key: str, value, track: bool = True):
    ex("INSERT OR REPLACE INTO pengaturan (kunci, nilai) VALUES (?,?)", (key, json.dumps(value)), track=track)


SET, MASTER = load_settings()
PAL = PALETTES.get(SET.get("palette"), PALETTES["Biru Samudra & Emas"])


def S(key):
    return SET.get(key, DEFAULTS.get(key))


# ---------- backup / restore ----------
def db_bytes() -> bytes:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    try:
        with LOCK:
            CONN.commit()
            dst = sqlite3.connect(tmp.name)
            CONN.backup(dst)
            dst.close()
        with open(tmp.name, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(tmp.name)
        except Exception:
            pass


def restore_db(data: bytes) -> tuple[bool, str]:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    try:
        tmp.write(data)
        tmp.close()
        src = sqlite3.connect(tmp.name)
        names = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if not names.intersection(set(TABLES.keys())):
            src.close()
            return False, "File bukan backup database SIMKERMA yang valid."
        with LOCK:
            CONN.commit()
            src.backup(CONN)
        src.close()
        init_db()
        return True, "Database berhasil dipulihkan."
    except Exception as e:
        return False, f"Gagal memulihkan database: {e}"
    finally:
        try:
            os.remove(tmp.name)
        except Exception:
            pass


def gh_cfg():
    tok, repo = get_secret("GITHUB_TOKEN"), get_secret("GITHUB_REPO")
    if not tok or not repo:
        return None
    return {"token": str(tok), "repo": str(repo).strip("/"),
            "branch": str(get_secret("GITHUB_BRANCH", "main")),
            "path": str(get_secret("GITHUB_BACKUP_PATH", "backup/kerjasama_sdmk.db")).strip("/")}


def _gh_headers(cfg, raw=False):
    return {"Authorization": f"Bearer {cfg['token']}",
            "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def gh_backup() -> tuple[bool, str]:
    cfg = gh_cfg()
    if not cfg:
        return False, "GitHub belum dikonfigurasi di Secrets."
    try:
        import requests
        url = f"https://api.github.com/repos/{cfg['repo']}/contents/{cfg['path']}"
        r = requests.get(url, headers=_gh_headers(cfg), params={"ref": cfg["branch"]}, timeout=30)
        sha = r.json().get("sha") if r.status_code == 200 else None
        if r.status_code not in (200, 404):
            return False, f"GitHub menolak akses ({r.status_code}): {r.text[:150]}"
        payload = {"message": f"Backup SIMKERMA {now_str()} WIB",
                   "content": base64.b64encode(db_bytes()).decode("ascii"),
                   "branch": cfg["branch"]}
        if sha:
            payload["sha"] = sha
        r2 = requests.put(url, headers=_gh_headers(cfg), json=payload, timeout=90)
        if r2.status_code in (200, 201):
            STATE["dirty"] = False
            STATE["last_backup"] = time.time()
            return True, "Backup ke GitHub berhasil."
        return False, f"Backup gagal ({r2.status_code}): {r2.text[:150]}"
    except Exception as e:
        return False, f"Backup gagal: {e}"


def gh_restore() -> tuple[bool, str]:
    cfg = gh_cfg()
    if not cfg:
        return False, "GitHub belum dikonfigurasi di Secrets."
    try:
        import requests
        url = f"https://api.github.com/repos/{cfg['repo']}/contents/{cfg['path']}"
        r = requests.get(url, headers=_gh_headers(cfg, raw=True), params={"ref": cfg["branch"]}, timeout=60)
        if r.status_code == 404:
            return False, "File backup belum ada di repository."
        if r.status_code != 200:
            return False, f"Gagal mengambil backup ({r.status_code})."
        return restore_db(r.content)
    except Exception as e:
        return False, f"Restore gagal: {e}"


def count_rows() -> int:
    total = 0
    for t in TABLES:
        try:
            total += int(q(f"SELECT COUNT(*) AS n FROM {t}")["n"].iloc[0])
        except Exception:
            pass
    return total


@st.cache_resource(show_spinner=False)
def boot_restore_once(marker: str) -> str:
    """Dijalankan sekali per proses: bila DB kosong & GitHub terhubung -> pulihkan otomatis."""
    try:
        if gh_cfg() and count_rows() == 0:
            ok, msg = gh_restore()
            return msg if ok else ""
    except Exception:
        pass
    return ""


def maybe_autobackup():
    try:
        if not S("auto_backup") or not gh_cfg() or not STATE.get("dirty"):
            return
        interval = max(1, int(S("backup_interval_min") or 30)) * 60
        if time.time() - float(STATE.get("last_backup", 0)) < interval:
            return
        ok, msg = gh_backup()
        st.toast(("☁️ " if ok else "⚠️ ") + msg)
    except Exception:
        pass


# =============================================================================
# 5. ENRICH (kolom turunan) & TAMPILAN TABEL
# =============================================================================
def status_dok(r: dict) -> str:
    st_ = clean(r.get("status")) or ""
    if st_ == "Dihentikan":
        return "Dihentikan"
    if st_ == "Draft/Proses":
        return "Dalam proses"
    if truthy(r.get("tanpa_batas")):
        return "Berlaku (tanpa batas)"
    end, start = parse_date(r.get("tgl_berakhir")), parse_date(r.get("tgl_mulai"))
    if not end:
        return "Tanpa tanggal"
    left = (end - TODAY).days
    if left < 0:
        return "Kadaluarsa"
    if start and start > TODAY:
        return "Belum mulai"
    if left <= int(S("kritis_days")):
        return "Kritis"
    if left <= int(S("warn_days")):
        return "Segera berakhir"
    return "Berlaku"


def status_waktu(status, start, end, done_set) -> str:
    status = clean(status) or ""
    if status in done_set:
        return "Selesai"
    if status in CLOSED:
        return status
    start, end = parse_date(start), parse_date(end)
    if end and end < TODAY:
        return "Lewat jadwal"
    if start and start > TODAY:
        return "Akan datang"
    if start and end and start <= TODAY <= end:
        return "Sedang berjalan"
    return "-"


def enrich(table: str, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    recs = df.to_dict("records")
    if table == "dokumen":
        df["masa_berlaku"] = [status_dok(r) for r in recs]
        df["sisa_hari"] = days_left(df["tgl_berakhir"])
    elif table == "praktik":
        df["waktu"] = [status_waktu(r.get("status"), r.get("tgl_mulai"), r.get("tgl_selesai"), DONE_PRAKTIK) for r in recs]
        df["sisa_hari"] = days_left(df["tgl_selesai"])
    elif table == "penelitian":
        df["waktu"] = [status_waktu(r.get("status"), r.get("tgl_mulai"), r.get("tgl_selesai"), DONE_RISET) for r in recs]
        df["sisa_hari"] = days_left(df["tgl_selesai"])
        tertunda = []
        for r in recs:
            end = parse_date(r.get("tgl_selesai"))
            stt = clean(r.get("status")) or ""
            tertunda.append(bool(end and end < TODAY and not truthy(r.get("laporan_diserahkan"))
                                 and stt not in CLOSED and stt not in EARLY_RISET))
        df["laporan_tertunda"] = tertunda
    elif table == "tugas":
        lab, dl = [], []
        for r in recs:
            due = parse_date(r.get("jatuh_tempo"))
            if (clean(r.get("status")) or "") == "Selesai":
                lab.append("Selesai")
            elif due and due < TODAY:
                lab.append("Terlambat")
            elif due and (due - TODAY).days <= 7:
                lab.append("Jatuh tempo ≤7 hari")
            else:
                lab.append("Terjadwal")
        df["waktu"] = lab
        df["sisa_hari"] = days_left(df["jatuh_tempo"])
    return df


def display_df(table: str, df: pd.DataFrame, all_fields: bool = False, plain: bool = False) -> pd.DataFrame:
    cfg = TABLES[table]
    out = pd.DataFrame(index=df.index)
    out["ID"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    for f in cfg["fields"]:
        if not (f.get("show") or all_fields):
            continue
        s = df[f["key"]]
        name = f["label"] if all_fields else f.get("short", f["label"])
        t = f["type"]
        if t == "date":
            s = fmt_dates(s)
        elif t == "bool":
            s = s.map(lambda x: "Ya" if truthy(x) else "Tidak")
        elif t == "int":
            s = pd.to_numeric(s, errors="coerce").fillna(0).astype(int)
        elif t == "number":
            s = s.map(rupiah)
        else:
            s = s.fillna("").astype(str)
        out[name] = s
    for key, label in COMPUTED_SHOW.get(table, []):
        if key in df.columns:
            s = df[key]
            if key in ("masa_berlaku", "waktu") and not plain:
                s = s.map(badge)
            out[label] = s
    return out


def column_cfg(table: str) -> dict:
    cfg = {}
    for f in TABLES[table]["fields"]:
        if f["type"] == "url" and f.get("show"):
            try:
                cfg[f.get("short", f["label"])] = st.column_config.LinkColumn(f.get("short", f["label"]))
            except Exception:
                pass
    return cfg


# =============================================================================
# 6. FORM GENERIK + VALIDASI
# =============================================================================
def doc_options() -> list[str]:
    df = load("dokumen")
    return [record_label("dokumen", r) for r in df.to_dict("records")]


def field_widget(f: dict, val, key: str):
    t = f["type"]
    label = f["label"] + (" *" if f.get("req") else "")
    help_ = f.get("help")
    if t in ("text", "url"):
        return st.text_input(label, value=str(clean(val) or ""), key=key, help=help_)
    if t == "textarea":
        return st.text_area(label, value=str(clean(val) or ""), key=key, height=90, help=help_)
    if t == "select":
        opts = list(MASTER.get(f["opts"], [])) or ["-"]
        cur = clean(val)
        if cur and cur not in opts:
            opts = [cur] + opts
        idx = opts.index(cur) if cur in opts else 0
        return st.selectbox(label, opts, index=idx, key=key, help=help_)
    if t == "ref_dokumen":
        opts = [""] + doc_options()
        cur = clean(val) or ""
        if cur and cur not in opts:
            opts.insert(1, cur)
        return st.selectbox(label, opts, index=opts.index(cur) if cur in opts else 0, key=key,
                            format_func=lambda x: x or "— tidak ada —", help=help_)
    if t == "date":
        d = parse_date(val)
        if d is None and f.get("req"):
            d = TODAY
        return st.date_input(label, value=d, key=key, format="DD/MM/YYYY", help=help_)
    if t == "int":
        try:
            v = int(float(clean(val) or f.get("min", 0)))
        except Exception:
            v = int(f.get("min", 0))
        return st.number_input(label, min_value=int(f.get("min", 0)), value=max(v, int(f.get("min", 0))),
                               step=1, key=key, help=help_)
    if t == "number":
        try:
            v = float(clean(val) or 0.0)
        except Exception:
            v = 0.0
        return st.number_input(label, min_value=0.0, value=v, step=10000.0, format="%.0f", key=key, help=help_)
    if t == "bool":
        return st.checkbox(label, value=truthy(val), key=key, help=help_)
    return None


def render_form(table: str, values: dict, prefix: str) -> dict:
    fields = TABLES[table]["fields"]
    normal = [f for f in fields if not f.get("full")]
    full = [f for f in fields if f.get("full")]
    data = {}
    c1, c2 = st.columns(2)
    for i, f in enumerate(normal):
        with (c1 if i % 2 == 0 else c2):
            data[f["key"]] = field_widget(f, values.get(f["key"]), f"{prefix}_{f['key']}")
    for f in full:
        data[f["key"]] = field_widget(f, values.get(f["key"]), f"{prefix}_{f['key']}")
    return data


def validate(table: str, data: dict, rid: int | None = None) -> list[str]:
    errs = []
    for f in TABLES[table]["fields"]:
        v = data.get(f["key"])
        if f.get("req"):
            if f["type"] in ("text", "select", "textarea", "url") and not str(clean(v) or "").strip():
                errs.append(f"**{f['label']}** wajib diisi.")
            elif f["type"] == "date" and parse_date(v) is None:
                errs.append(f"**{f['label']}** wajib diisi.")
            elif f["type"] == "int" and int(clean(v) or 0) < int(f.get("min", 0)):
                errs.append(f"**{f['label']}** minimal {f.get('min', 0)}.")
        if f["type"] == "url" and str(clean(v) or "").strip():
            if not str(v).strip().lower().startswith(("http://", "https://")):
                errs.append(f"**{f['label']}** harus diawali http:// atau https://")
        if f.get("strict") and f["type"] == "select":
            if str(clean(v) or "") not in MASTER.get(f["opts"], []):
                errs.append(f"**{f['label']}** harus salah satu dari: {', '.join(MASTER.get(f['opts'], []))}.")
    d = lambda k: parse_date(data.get(k))
    if table == "dokumen":
        if not truthy(data.get("tanpa_batas")) and d("tgl_berakhir") is None:
            errs.append("**Tanggal Berakhir** wajib diisi, atau centang *Berlaku tanpa batas waktu*.")
        if d("tgl_berakhir") and d("tgl_mulai") and d("tgl_berakhir") < d("tgl_mulai"):
            errs.append("Tanggal berakhir tidak boleh lebih awal dari tanggal mulai.")
        nomor = str(clean(data.get("nomor")) or "").strip()
        if nomor:
            dup = q("SELECT id FROM dokumen WHERE lower(nomor)=? AND lower(jenis)=? AND id<>?",
                    (nomor.lower(), str(data.get("jenis") or "").lower(), int(rid or 0)))
            if not dup.empty:
                errs.append(f"Nomor dokumen **{nomor}** untuk jenis yang sama sudah terdaftar (ID #{int(dup['id'].iloc[0])}).")
    if table in ("praktik", "penelitian"):
        if d("tgl_mulai") and d("tgl_selesai") and d("tgl_selesai") < d("tgl_mulai"):
            errs.append("Tanggal selesai tidak boleh lebih awal dari tanggal mulai.")
    return errs


def flash(kind: str, msg: str):
    st.session_state["flash"] = (kind, msg)


def show_flash():
    f = st.session_state.pop("flash", None)
    if f:
        kind, msg = f
        {"success": st.success, "error": st.error, "warning": st.warning}.get(kind, st.info)(msg)


# =============================================================================
# 7. LAMPIRAN
# =============================================================================
def attachments_ui(table: str, rid: int, can_edit: bool, ctx: str = "d"):
    st.markdown("##### 📎 Lampiran Dokumen")
    att = q("SELECT id, nama, tipe, ukuran, dibuat FROM lampiran WHERE modul=? AND ref_id=? ORDER BY id DESC",
            (table, int(rid)))
    if att.empty:
        st.caption("Belum ada lampiran.")
    for r in att.to_dict("records"):
        c1, c2, c3 = st.columns([5, 2, 1])
        c1.markdown(f"📄 {r['nama']}  \n<small>{int(r['ukuran'] or 0) / 1024:,.0f} KB · {r['dibuat']}</small>", unsafe_allow_html=True)
        row = q("SELECT data FROM lampiran WHERE id=?", (int(r["id"]),))
        blob = row["data"].iloc[0] if not row.empty else b""
        c2.download_button("⬇️ Unduh", data=bytes(blob or b""), file_name=str(r["nama"]),
                           key=f"dl_{ctx}_{table}_{rid}_{r['id']}", **WB)
        if can_edit and c3.button("🗑️", key=f"delatt_{ctx}_{table}_{rid}_{r['id']}", help="Hapus lampiran"):
            ex("DELETE FROM lampiran WHERE id=?", (int(r["id"]),))
            log_event("Hapus Lampiran", table, str(rid), str(r["nama"]))
            flash("success", "Lampiran dihapus.")
            st.rerun()
    if can_edit:
        nonce = st.session_state.get("att_nonce", 0)
        up = st.file_uploader("Unggah lampiran (PDF, Word, Excel, gambar)", key=f"up_{ctx}_{table}_{rid}_{nonce}",
                              type=["pdf", "doc", "docx", "xls", "xlsx", "jpg", "jpeg", "png"])
        if up is not None and st.button("Simpan lampiran", key=f"saveatt_{ctx}_{table}_{rid}_{nonce}"):
            data = up.getvalue()
            limit = float(S("max_upload_mb") or 5) * 1024 * 1024
            if len(data) > limit:
                st.error(f"Ukuran file melebihi batas {S('max_upload_mb')} MB.")
            else:
                ex("INSERT INTO lampiran (modul, ref_id, nama, tipe, ukuran, data, dibuat) VALUES (?,?,?,?,?,?,?)",
                   (table, int(rid), up.name, up.type or "", len(data), sqlite3.Binary(data), now_str()))
                log_event("Unggah Lampiran", table, str(rid), up.name)
                st.session_state["att_nonce"] = nonce + 1
                flash("success", "Lampiran tersimpan.")
                st.rerun()


# =============================================================================
# 8. EKSPOR
# =============================================================================
def to_excel_bytes(sheets: dict, keep_empty: bool = False) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        for name, df in sheets.items():
            if df is not None and (not df.empty or (keep_empty and len(df.columns) > 0)):
                d = df
            else:
                d = pd.DataFrame({"Info": ["Belum ada data"]})
            d.to_excel(writer, sheet_name=str(name)[:31], index=False)
            try:
                from openpyxl.styles import Alignment, Font, PatternFill
                ws = writer.sheets[str(name)[:31]]
                fill = PatternFill("solid", fgColor=PAL["primary"].lstrip("#"))
                for cell in ws[1]:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = fill
                    cell.alignment = Alignment(vertical="center", wrap_text=True)
                for col in ws.columns:
                    width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                    ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 50)
                ws.freeze_panes = "A2"
            except Exception:
                pass
    return bio.getvalue()


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def full_export(table: str) -> pd.DataFrame:
    return display_df(table, enrich(table, load(table)), all_fields=True, plain=True)


def esc_ics(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def make_ics(events: pd.DataFrame) -> bytes:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SIMKERMA SDMK//ID", "CALSCALE:GREGORIAN"]
    for i, e in enumerate(events.to_dict("records")):
        d = e["tanggal"]
        uid = hashlib.md5(f"{d}{e['kategori']}{e['keterangan']}{i}".encode()).hexdigest()
        lines += ["BEGIN:VEVENT", f"UID:{uid}@simkerma", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{(d + timedelta(days=1)):%Y%m%d}",
                  f"SUMMARY:{esc_ics(e['kategori'] + ': ' + e['keterangan'])}",
                  f"DESCRIPTION:{esc_ics('PJ: ' + str(e.get('pj') or '-'))}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


# =============================================================================
# 9. TEMA / CSS
# =============================================================================
def inject_css():
    css = """
<style>
.stApp { background: %BG%; color: %TEXT%; }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.3rem; padding-bottom: 3rem; max-width: 1450px; }
h1, h2, h3, h4, h5 { color: %PRIMARY%; letter-spacing: .1px; }
section[data-testid="stSidebar"] { background: linear-gradient(185deg, %PRIMARY% 0%, %SECONDARY% 100%); }
section[data-testid="stSidebar"] * { color: #F4F7FA !important; }
section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.22); }
section[data-testid="stSidebar"] .stButton > button { background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.4); border-radius: 8px; }
section[data-testid="stSidebar"] .stButton > button:hover { background: rgba(255,255,255,.22); }
section[data-testid="stSidebar"] div[role="radiogroup"] label { padding: 6px 10px; border-radius: 8px; margin-bottom: 2px; }
section[data-testid="stSidebar"] div[role="radiogroup"] label:hover { background: rgba(255,255,255,.14); }
.side-brand { font-weight: 700; font-size: 1.15rem; line-height: 1.25; }
.side-sub { font-size: .8rem; opacity: .85; }
.hero { background: linear-gradient(120deg, %PRIMARY% 0%, %SECONDARY% 100%); border-bottom: 4px solid %ACCENT%; border-radius: 16px; padding: 22px 28px; color: #fff; display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 18px; box-shadow: 0 4px 14px rgba(16,24,40,.12); }
.hero h1 { color: #fff !important; margin: 0; font-size: 1.65rem; }
.hero p { margin: 4px 0 0 0; color: #E8EEF4; font-size: .95rem; }
.hero-date { background: rgba(255,255,255,.14); padding: 8px 14px; border-radius: 10px; font-weight: 600; font-size: .9rem; color: #fff; }
[data-testid="stMetric"] { background: #fff; border: 1px solid #E3E8EE; border-left: 5px solid %ACCENT%; border-radius: 12px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(16,24,40,.06); }
[data-testid="stMetricLabel"] p { color: #5B6B7B; font-weight: 600; font-size: .85rem; }
[data-testid="stMetricValue"] { color: %PRIMARY%; font-weight: 700; }
.stTabs [data-baseweb="tab"] { font-weight: 600; }
.stTabs [aria-selected="true"] { color: %PRIMARY% !important; }
.stTabs [data-baseweb="tab-highlight"] { background-color: %ACCENT% !important; }
button[kind="primary"], button[kind="primaryFormSubmit"], [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] { background: %PRIMARY% !important; color: #fff !important; border: 1px solid %PRIMARY% !important; border-radius: 8px; font-weight: 600; }
button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover, [data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover { background: %SECONDARY% !important; border-color: %SECONDARY% !important; }
.stDownloadButton > button, .stButton > button { border-radius: 8px; }
[data-testid="stForm"] { background: #fff; border: 1px solid #E3E8EE; border-radius: 14px; padding: 18px 20px; }
[data-testid="stExpander"] { background: #fff; border-radius: 12px; border: 1px solid #E3E8EE; }
.sw { display:inline-block; width:34px; height:34px; border-radius:8px; margin-right:6px; border:1px solid #d0d7de; vertical-align:middle; }
.small-muted { color:#6b7a8a; font-size:.82rem; }
</style>
"""
    for k, v in {"%PRIMARY%": PAL["primary"], "%SECONDARY%": PAL["secondary"], "%ACCENT%": PAL["accent"],
                 "%BG%": PAL["bg"], "%TEXT%": PAL["text"]}.items():
        css = css.replace(k, v)
    st.markdown(css, unsafe_allow_html=True)


def hero():
    st.markdown(
        f'<div class="hero"><div><h1>🏛️ {html.escape(str(S("app_name")))}</h1>'
        f'<p>{html.escape(str(S("instansi")))} · {html.escape(str(S("unit")))}</p></div>'
        f'<div class="hero-date">📅 {long_date(TODAY)}</div></div>',
        unsafe_allow_html=True)


def page_header(title: str, subtitle: str = ""):
    st.markdown(f"## {title}")
    if subtitle:
        st.caption(subtitle)


# =============================================================================
# 10. AUTENTIKASI
# =============================================================================
def safe_eq(a, b) -> bool:
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def hash_pw(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()


def auth_required() -> bool:
    return bool(get_secret("ADMIN_PASSWORD") or get_secret("VIEWER_PASSWORD") or SET.get("auth_admin") or SET.get("auth_viewer"))


def check_login(pw: str) -> str | None:
    salt = str(SET.get("auth_salt", ""))
    adm_env, vw_env = get_secret("ADMIN_PASSWORD"), get_secret("VIEWER_PASSWORD")
    if adm_env and safe_eq(adm_env, pw):
        return "admin"
    if SET.get("auth_admin") and safe_eq(SET["auth_admin"], hash_pw(pw, salt)):
        return "admin"
    if vw_env and safe_eq(vw_env, pw):
        return "viewer"
    if SET.get("auth_viewer") and safe_eq(SET["auth_viewer"], hash_pw(pw, salt)):
        return "viewer"
    return None


def login_screen():
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        st.markdown(f"<h2 style='text-align:center;margin-top:8vh'>🏛️ {html.escape(str(S('app_name')))}</h2>"
                    f"<p style='text-align:center' class='small-muted'>{html.escape(str(S('instansi')))} · {html.escape(str(S('unit')))}</p>",
                    unsafe_allow_html=True)
        tries = st.session_state.get("login_fail", 0)
        with st.form("login"):
            pw = st.text_input("Password", type="password")
            go_ = st.form_submit_button("Masuk", type="primary", **WB)
        if go_:
            if tries >= 5:
                st.error("Terlalu banyak percobaan gagal. Muat ulang halaman untuk mencoba lagi.")
            else:
                role = check_login(pw or "")
                if role:
                    st.session_state["role"] = role
                    st.session_state["login_fail"] = 0
                    st.rerun()
                else:
                    st.session_state["login_fail"] = tries + 1
                    st.error("Password salah.")
        st.caption("Lupa password? Atur `ADMIN_PASSWORD` pada Secrets Streamlit untuk akses darurat.")


# =============================================================================
# 11. CHART HELPERS
# =============================================================================
_FIG_N = {"n": 0}


def show_fig(fig, title: str | None = None, height: int = 340):
    colorway = [PAL["primary"], PAL["secondary"], PAL["accent"], OK_C, "#8E7CC3", "#E4572E", "#5DADE2", "#7F8C9A"]
    layout = dict(height=height, margin=dict(l=10, r=10, t=55 if title else 20, b=10),
                  paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                  font=dict(color=PAL["text"]), colorway=colorway,
                  legend=dict(orientation="h", yanchor="top", y=-0.18, x=0))
    if title:
        layout["title"] = dict(text=title, x=0.01, font=dict(size=15, color=PAL["primary"]))
    fig.update_layout(**layout)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#E6EAF0")
    _FIG_N["n"] += 1
    st.plotly_chart(fig, key=f"fig_{_FIG_N['n']}", **WP)


def empty_note(msg="Belum ada data untuk ditampilkan."):
    st.info(msg)


def mini_table(df: pd.DataFrame):
    if df.empty:
        st.success("Tidak ada item yang perlu perhatian. ✅")
    else:
        st.dataframe(df, hide_index=True, **WD)


# =============================================================================
# 12. HALAMAN: BERANDA
# =============================================================================
def page_home():
    hero()
    show_flash()
    dok = enrich("dokumen", load("dokumen"))
    pra = enrich("praktik", load("praktik"))
    pen = enrich("penelitian", load("penelitian"))
    tug = enrich("tugas", load("tugas"))

    years = set()
    for df, col in ((pra, "tgl_mulai"), (pen, "tgl_mulai")):
        yrs = pd.to_datetime(df[col], errors="coerce").dt.year.dropna().astype(int).tolist() if not df.empty else []
        years.update(yrs)
    year_opts = ["Semua tahun"] + [str(y) for y in sorted(years, reverse=True)]
    colf, _ = st.columns([1, 3])
    year_sel = colf.selectbox("Periode (untuk grafik Praktik & Penelitian)", year_opts, key="home_year")
    if year_sel != "Semua tahun":
        y = int(year_sel)
        if not pra.empty:
            pra = pra[pd.to_datetime(pra["tgl_mulai"], errors="coerce").dt.year == y]
        if not pen.empty:
            pen = pen[pd.to_datetime(pen["tgl_mulai"], errors="coerce").dt.year == y]

    # ----- KPI -----
    aktif_set = {"Berlaku", "Berlaku (tanpa batas)", "Segera berakhir", "Kritis"}
    n_total = len(dok)
    n_aktif = int(dok["masa_berlaku"].isin(aktif_set).sum()) if n_total else 0
    n_segera = int(dok["masa_berlaku"].isin({"Segera berakhir", "Kritis"}).sum()) if n_total else 0
    n_kadal = int((dok["masa_berlaku"] == "Kadaluarsa").sum()) if n_total else 0
    n_pra = int((pra["waktu"] == "Sedang berjalan").sum()) if len(pra) else 0
    n_mhs = int(pd.to_numeric(pra.loc[pra["waktu"] == "Sedang berjalan", "jumlah"], errors="coerce").fillna(0).sum()) if len(pra) else 0
    n_pen = int((pen["waktu"] == "Sedang berjalan").sum()) if len(pen) else 0
    open_t = tug[tug["status"] != "Selesai"] if len(tug) else tug
    n_open = len(open_t)
    n_late = int((tug["waktu"] == "Terlambat").sum()) if len(tug) else 0

    c = st.columns(6)
    c[0].metric("Total Dokumen", n_total, help="MoU + PKS + KB yang terdaftar")
    c[1].metric("Dokumen Berlaku", n_aktif)
    c[2].metric("Segera Berakhir", n_segera, help=f"≤ {S('warn_days')} hari")
    c[3].metric("Kadaluarsa", n_kadal)
    c[4].metric("Praktik Berjalan", n_pra, f"{n_mhs} mahasiswa", delta_color="off")
    c[5].metric("Penelitian Berjalan", n_pen)
    c2 = st.columns(6)
    j = dok["jenis"].value_counts() if n_total else pd.Series(dtype=int)
    c2[0].metric("MoU", int(j.get("MoU", 0)))
    c2[1].metric("PKS", int(j.get("PKS", 0)))
    c2[2].metric("Kesepakatan Bersama", int(j.get("KB", 0)))
    c2[3].metric("Tugas Terbuka", n_open)
    c2[4].metric("Tugas Terlambat", n_late)
    n_lap = int(pen["laporan_tertunda"].sum()) if len(pen) else 0
    c2[5].metric("Laporan Penelitian Tertunda", n_lap)

    st.markdown("### ⚠️ Perlu Perhatian")
    t1, t2, t3, t4 = st.tabs(["📄 Dokumen", "✅ Tugas", "🎓 Praktik", "🔬 Penelitian"])
    with t1:
        if n_total:
            d = dok[dok["masa_berlaku"].isin(["Kritis", "Segera berakhir", "Kadaluarsa"])].copy()
            d["_s"] = pd.to_numeric(d["sisa_hari"], errors="coerce")
            d = d.sort_values("_s").head(60)
            out = pd.DataFrame({"Jenis": d["jenis"], "Nomor": d["nomor"].fillna(""), "Mitra": d["mitra"].fillna(""),
                                "Berakhir": fmt_dates(d["tgl_berakhir"]), "Sisa Hari": d["sisa_hari"],
                                "Status": d["masa_berlaku"].map(badge), "PJ": d["pj"].fillna("")})
            mini_table(out)
        else:
            empty_note()
    with t2:
        if len(tug):
            d = tug[(tug["status"] != "Selesai") & (tug["waktu"].isin(["Terlambat", "Jatuh tempo ≤7 hari"]))].copy()
            d = d.sort_values("jatuh_tempo")
            out = pd.DataFrame({"Tugas": d["judul"], "Modul": d["modul"], "Jatuh Tempo": fmt_dates(d["jatuh_tempo"]),
                                "Sisa Hari": d["sisa_hari"], "Prioritas": d["prioritas"], "Status": d["waktu"].map(badge),
                                "PJ": d["pj"].fillna("")})
            mini_table(out)
        else:
            empty_note()
    with t3:
        if len(pra):
            soon = pra[(pra["waktu"] == "Sedang berjalan") & (pd.to_numeric(pra["sisa_hari"], errors="coerce") <= 14)]
            belum = pra[(pra["status"] == "Selesai") & (~pra["evaluasi_diterima"].map(truthy))]
            late = pra[pra["waktu"] == "Lewat jadwal"]
            d = pd.concat([soon.assign(Catatan="Berakhir ≤14 hari"), late.assign(Catatan="Lewat jadwal, status belum selesai"),
                           belum.assign(Catatan="Evaluasi belum diterima")]).drop_duplicates(subset=["id", "Catatan"])
            out = pd.DataFrame({"Institusi": d["institusi"], "Prodi": d["prodi"], "Lokasi": d["lokasi"],
                                "Selesai": fmt_dates(d["tgl_selesai"]), "Status": d["status"], "Catatan": d["Catatan"]})
            mini_table(out)
        else:
            empty_note()
    with t4:
        if len(pen):
            d = pen[pen["laporan_tertunda"] | ((pen["waktu"] == "Sedang berjalan") & (pd.to_numeric(pen["sisa_hari"], errors="coerce") <= 14))]
            out = pd.DataFrame({"Peneliti": d["peneliti"], "Judul": d["judul"], "Institusi": d["institusi"],
                                "Selesai": fmt_dates(d["tgl_selesai"]), "Status": d["status"],
                                "Catatan": d["laporan_tertunda"].map(lambda x: "Laporan belum diserahkan" if x else "Berakhir ≤14 hari")})
            mini_table(out)
        else:
            empty_note()

    st.markdown("### 📈 Analitik")
    g1, g2, g3, g4 = st.tabs(["🤝 Kerja Sama", "🎓 Praktik Mahasiswa", "🔬 Penelitian", "🗓️ Linimasa"])
    with g1:
        if n_total:
            a, b = st.columns(2)
            with a:
                cnt = dok["masa_berlaku"].value_counts().reset_index()
                cnt.columns = ["Status", "Jumlah"]
                fig = px.pie(cnt, names="Status", values="Jumlah", hole=0.55, color="Status", color_discrete_map=MASA_COLOR)
                show_fig(fig, "Masa Berlaku Dokumen")
            with b:
                cj = dok.groupby(["jenis", "masa_berlaku"]).size().reset_index(name="Jumlah")
                fig = px.bar(cj, x="jenis", y="Jumlah", color="masa_berlaku", color_discrete_map=MASA_COLOR,
                             labels={"jenis": "Jenis", "masa_berlaku": "Status"})
                show_fig(fig, "Dokumen per Jenis & Status")
            a, b = st.columns(2)
            with a:
                e = dok[(dok["status"] != "Dihentikan") & (~dok["tanpa_batas"].map(truthy))].copy()
                e["bln"] = pd.to_datetime(e["tgl_berakhir"], errors="coerce").dt.strftime("%Y-%m")
                months = [str(p) for p in pd.period_range(pd.Timestamp(TODAY), periods=12, freq="M")]
                m = e["bln"].value_counts().reindex(months).fillna(0).astype(int).reset_index()
                m.columns = ["Bulan", "Jumlah"]
                fig = px.bar(m, x="Bulan", y="Jumlah")
                fig.update_traces(marker_color=PAL["accent"])
                show_fig(fig, "Dokumen Berakhir — 12 Bulan ke Depan")
            with b:
                tm = dok["mitra"].value_counts().head(10).reset_index()
                tm.columns = ["Mitra", "Jumlah"]
                fig = px.bar(tm, x="Jumlah", y="Mitra", orientation="h")
                fig.update_traces(marker_color=PAL["secondary"])
                fig.update_yaxes(autorange="reversed")
                show_fig(fig, "10 Mitra dengan Dokumen Terbanyak")
            bd = dok["bidang"].replace("", pd.NA).dropna().value_counts().reset_index()
            if not bd.empty:
                bd.columns = ["Bidang", "Jumlah"]
                fig = px.bar(bd, x="Jumlah", y="Bidang", orientation="h")
                fig.update_traces(marker_color=PAL["primary"])
                fig.update_yaxes(autorange="reversed")
                show_fig(fig, "Dokumen per Bidang Kerja Sama", 320)
        else:
            empty_note()
    with g2:
        if len(pra):
            p = pra.copy()
            p["jumlah"] = pd.to_numeric(p["jumlah"], errors="coerce").fillna(0)
            a, b = st.columns(2)
            with a:
                ti = p.groupby("institusi")["jumlah"].sum().sort_values(ascending=False).head(10).reset_index()
                fig = px.bar(ti, x="jumlah", y="institusi", orientation="h", labels={"jumlah": "Mahasiswa", "institusi": "Institusi"})
                fig.update_traces(marker_color=PAL["secondary"])
                fig.update_yaxes(autorange="reversed")
                show_fig(fig, "Mahasiswa per Institusi (Top 10)")
            with b:
                sp = p["status"].value_counts().reset_index()
                sp.columns = ["Status", "Jumlah"]
                fig = px.pie(sp, names="Status", values="Jumlah", hole=0.55)
                show_fig(fig, "Status Pengajuan Praktik")
            a, b = st.columns(2)
            with a:
                p["bln"] = pd.to_datetime(p["tgl_mulai"], errors="coerce").dt.strftime("%Y-%m")
                pm = p.dropna(subset=["bln"]).groupby("bln")["jumlah"].sum().reset_index()
                fig = px.bar(pm, x="bln", y="jumlah", labels={"bln": "Bulan Mulai", "jumlah": "Mahasiswa"})
                fig.update_traces(marker_color=PAL["accent"])
                show_fig(fig, "Mahasiswa Mulai Praktik per Bulan")
            with b:
                pj = p.groupby("jenjang")["jumlah"].sum().reset_index()
                fig = px.bar(pj, x="jenjang", y="jumlah", labels={"jenjang": "Jenjang", "jumlah": "Mahasiswa"})
                fig.update_traces(marker_color=PAL["primary"])
                show_fig(fig, "Mahasiswa per Jenjang")
            lk = p.groupby("lokasi")["jumlah"].sum().sort_values(ascending=False).head(10).reset_index()
            fig = px.bar(lk, x="jumlah", y="lokasi", orientation="h", labels={"jumlah": "Mahasiswa", "lokasi": "Lokasi"})
            fig.update_traces(marker_color=PAL["secondary"])
            fig.update_yaxes(autorange="reversed")
            show_fig(fig, "Lokasi Praktik Terpadat (Top 10)", 320)
        else:
            empty_note()
    with g3:
        if len(pen):
            a, b = st.columns(2)
            with a:
                sp = pen["status"].value_counts().reset_index()
                sp.columns = ["Status", "Jumlah"]
                fig = px.pie(sp, names="Status", values="Jumlah", hole=0.55)
                show_fig(fig, "Status Penelitian")
            with b:
                jp = pen["jenis"].value_counts().reset_index()
                jp.columns = ["Jenis", "Jumlah"]
                fig = px.bar(jp, x="Jenis", y="Jumlah")
                fig.update_traces(marker_color=PAL["secondary"])
                show_fig(fig, "Penelitian per Jenis")
            t = pen.copy()
            t["bln"] = pd.to_datetime(t["tgl_pengajuan"], errors="coerce").dt.strftime("%Y-%m")
            tm = t.dropna(subset=["bln"]).groupby("bln").size().reset_index(name="Jumlah")
            if not tm.empty:
                fig = px.bar(tm, x="bln", y="Jumlah", labels={"bln": "Bulan Pengajuan"})
                fig.update_traces(marker_color=PAL["accent"])
                show_fig(fig, "Pengajuan Penelitian per Bulan", 300)
            ti = pen["institusi"].value_counts().head(10).reset_index()
            ti.columns = ["Institusi", "Jumlah"]
            fig = px.bar(ti, x="Jumlah", y="Institusi", orientation="h")
            fig.update_traces(marker_color=PAL["primary"])
            fig.update_yaxes(autorange="reversed")
            show_fig(fig, "Institusi Peneliti Terbanyak (Top 10)", 320)
        else:
            empty_note()
    with g4:
        if n_total:
            d = dok[(dok["status"] != "Dihentikan") & (~dok["tanpa_batas"].map(truthy))].copy()
            d["start"] = pd.to_datetime(d["tgl_mulai"], errors="coerce")
            d["end"] = pd.to_datetime(d["tgl_berakhir"], errors="coerce")
            d = d.dropna(subset=["start", "end"])
            d = d[d["end"] >= d["start"]]
            d["_sisa"] = pd.to_numeric(d["sisa_hari"], errors="coerce")
            d = d.sort_values("_sisa", key=lambda s: s.abs()).head(25)
            if d.empty:
                empty_note("Belum ada dokumen dengan tanggal mulai & berakhir.")
            else:
                d["label"] = d["jenis"].astype(str) + " · " + d["mitra"].astype(str).str.slice(0, 32)
                fig = px.timeline(d, x_start="start", x_end="end", y="label", color="masa_berlaku",
                                  color_discrete_map=MASA_COLOR, labels={"masa_berlaku": "Status", "label": ""})
                fig.update_yaxes(autorange="reversed")
                t0 = pd.Timestamp(TODAY).strftime("%Y-%m-%d")
                fig.add_shape(type="line", x0=t0, x1=t0, y0=0, y1=1, yref="paper",
                              line=dict(color=BAD_C, width=2, dash="dash"))
                show_fig(fig, "Linimasa Dokumen (25 terdekat dari hari ini — garis merah = hari ini)", 120 + 26 * len(d))
        else:
            empty_note()


# =============================================================================
# 13. HALAMAN GENERIK MODUL (Dokumen / Praktik / Penelitian / Tugas)
# =============================================================================
def render_detail(table: str, rid: int):
    row = fetch_one(table, rid)
    if not row:
        st.warning("Data tidak ditemukan.")
        return
    enr = enrich(table, pd.DataFrame([row])).iloc[0].to_dict()
    cols = st.columns(2)
    items = []
    for f in TABLES[table]["fields"]:
        v = row.get(f["key"])
        if f["type"] == "date":
            txt = fmt_date(v)
        elif f["type"] == "bool":
            txt = "Ya" if truthy(v) else "Tidak"
        elif f["type"] == "number":
            txt = rupiah(v)
        elif f["type"] == "url" and clean(v):
            txt = f"[{v}]({v})"
        else:
            txt = str(clean(v) or "-")
        items.append((f["label"], txt))
    for key, label in COMPUTED_SHOW.get(table, []):
        val = clean(enr.get(key))
        items.append((label, badge(val) if key in ("masa_berlaku", "waktu") else str(val if val is not None else "-")))
    for i, (lab, txt) in enumerate(items):
        cols[i % 2].markdown(f"**{lab}**  \n{txt}")
    st.caption(f"Dibuat: {row.get('dibuat')} · Diubah: {row.get('diubah')}")


def tab_list(table: str, can_edit: bool):
    cfg = TABLES[table]
    df = enrich(table, load(table))
    if df.empty:
        st.info("Belum ada data. Gunakan tab **➕ Tambah**, atau menu **Pengaturan → Impor Data / Data Demo**.")
        return
    c1, c2, c3, c4 = st.columns([2, 1.3, 1.3, 1.3])
    search = c1.text_input("🔎 Cari", key=f"s_{table}", placeholder="ketik kata kunci apa saja…")
    sel = {}
    for (col, label), cc in zip(cfg["filters"], (c2, c3, c4)):
        opts = sorted({str(x) for x in df[col].dropna().tolist() if str(x) != ""})
        sel[col] = cc.multiselect(label, opts, key=f"f_{table}_{col}")
    mask = pd.Series(True, index=df.index)
    for col, vals in sel.items():
        if vals:
            mask &= df[col].isin(vals)
    if search.strip():
        tcols = [f["key"] for f in cfg["fields"] if f["type"] in ("text", "textarea", "select", "url")]
        hay = df[tcols].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        mask &= hay.str.contains(search.strip().lower(), regex=False)
    fdf = df[mask]
    st.caption(f"Menampilkan **{len(fdf)}** dari {len(df)} data.")
    disp = display_df(table, fdf)
    st.dataframe(disp, hide_index=True, column_config=column_cfg(table), **WD)

    d1, d2, _ = st.columns([1, 1, 4])
    plain = display_df(table, fdf, all_fields=True, plain=True)
    d1.download_button("⬇️ CSV", data=csv_bytes(plain), file_name=f"{table}_{TODAY}.csv", mime="text/csv",
                       key=f"csv_{table}", **WB)
    d2.download_button("⬇️ Excel", data=to_excel_bytes({TABLES[table]["singular"]: plain}),
                       file_name=f"{table}_{TODAY}.xlsx", key=f"xls_{table}", **WB)

    with st.expander("🔎 Lihat detail & lampiran"):
        if fdf.empty:
            st.caption("Tidak ada data pada filter ini.")
        else:
            labels = {int(r["id"]): record_label(table, r) for r in fdf.to_dict("records")}
            rid = st.selectbox("Pilih data", list(labels.keys()), format_func=lambda i: labels[i], key=f"det_{table}")
            render_detail(table, rid)
            st.divider()
            attachments_ui(table, rid, can_edit, "d")


def tab_add(table: str):
    nonce = st.session_state.get(f"nonce_{table}", 0)
    defaults = dict(TABLES[table]["new_defaults"])
    if any(f["key"] == "pj" for f in TABLES[table]["fields"]):
        defaults["pj"] = S("default_pj") or ""
    with st.form(f"form_add_{table}_{nonce}"):
        data = render_form(table, defaults, f"a_{table}_{nonce}")
        ok = st.form_submit_button("💾 Simpan Data", type="primary", **WB)
    if ok:
        errs = validate(table, data)
        if errs:
            for e in errs:
                st.error(e)
        else:
            new_id = save_record(table, data)
            st.session_state[f"nonce_{table}"] = nonce + 1
            flash("success", f"Data berhasil disimpan (ID #{new_id}). Lampiran dapat ditambahkan lewat tab 🔎 detail atau ✏️ Ubah.")
            st.rerun()


def tab_edit(table: str):
    df = load(table)
    if df.empty:
        st.info("Belum ada data untuk diubah.")
        return
    labels = {int(r["id"]): record_label(table, r) for r in df.to_dict("records")}
    rid = st.selectbox("Pilih data yang akan diubah / dihapus", list(labels.keys()),
                       format_func=lambda i: labels[i], key=f"pick_{table}")
    row = fetch_one(table, rid)
    nonce = st.session_state.get(f"enonce_{table}", 0)
    with st.form(f"form_edit_{table}_{rid}_{nonce}"):
        data = render_form(table, row, f"e_{table}_{rid}_{nonce}")
        ok = st.form_submit_button("💾 Simpan Perubahan", type="primary", **WB)
    if ok:
        errs = validate(table, data, rid)
        if errs:
            for e in errs:
                st.error(e)
        else:
            save_record(table, data, rid)
            st.session_state[f"enonce_{table}"] = nonce + 1
            flash("success", f"Perubahan ID #{rid} tersimpan.")
            st.rerun()
    st.divider()
    attachments_ui(table, rid, True, "e")
    with st.expander("🗑️ Hapus data ini"):
        st.warning("Data dan seluruh lampirannya akan dihapus permanen.")
        sure = st.checkbox("Saya yakin ingin menghapus data ini", key=f"sure_{table}_{rid}")
        if st.button("Hapus Permanen", key=f"del_{table}_{rid}", disabled=not sure):
            delete_record(table, rid)
            flash("success", f"Data ID #{rid} dihapus.")
            st.rerun()


def page_module(table: str, icon: str, subtitle: str):
    can_edit = st.session_state.get("role", "admin") == "admin"
    page_header(f"{icon} {TABLES[table]['title']}", subtitle)
    show_flash()
    if can_edit:
        t1, t2, t3 = st.tabs(["📋 Daftar", "➕ Tambah Baru", "✏️ Ubah / Hapus"])
        with t1:
            tab_list(table, True)
        with t2:
            tab_add(table)
        with t3:
            tab_edit(table)
    else:
        tab_list(table, False)


# =============================================================================
# 14. AGENDA & PENGINGAT
# =============================================================================
def build_events() -> pd.DataFrame:
    ev = []
    dok = load("dokumen")
    for r in dok.to_dict("records"):
        d = parse_date(r.get("tgl_berakhir"))
        if d and (clean(r.get("status")) or "") != "Dihentikan" and not truthy(r.get("tanpa_batas")):
            ev.append((d, "Dokumen Berakhir", f"{r.get('jenis')} {r.get('nomor') or '-'} — {r.get('mitra')}", r.get("pj"), "Dokumen"))
    for r in load("praktik").to_dict("records"):
        if (clean(r.get("status")) or "") in CLOSED | DONE_PRAKTIK:
            continue
        lab = f"{r.get('institusi')} · {r.get('prodi')} @ {r.get('lokasi')}"
        for k, kat in (("tgl_mulai", "Praktik Mulai"), ("tgl_selesai", "Praktik Selesai")):
            d = parse_date(r.get(k))
            if d:
                ev.append((d, kat, lab, r.get("pj"), "Praktik"))
    for r in load("penelitian").to_dict("records"):
        if (clean(r.get("status")) or "") in CLOSED | DONE_RISET:
            continue
        lab = f"{r.get('peneliti')} — {str(r.get('judul') or '')[:70]}"
        for k, kat in (("tgl_mulai", "Penelitian Mulai"), ("tgl_selesai", "Penelitian Selesai")):
            d = parse_date(r.get(k))
            if d:
                ev.append((d, kat, lab, r.get("pj"), "Penelitian"))
    for r in load("tugas").to_dict("records"):
        d = parse_date(r.get("jatuh_tempo"))
        if d and (clean(r.get("status")) or "") != "Selesai":
            ev.append((d, "Tugas Jatuh Tempo", str(r.get("judul") or ""), r.get("pj"), "Tugas"))
    df = pd.DataFrame(ev, columns=["tanggal", "kategori", "keterangan", "pj", "modul"])
    if not df.empty:
        df["pj"] = df["pj"].map(lambda x: clean(x) or "")
        df = df.sort_values("tanggal").reset_index(drop=True)
    return df


def page_agenda():
    page_header("📅 Agenda & Pengingat", "Semua tenggat dokumen, jadwal praktik, penelitian, dan tugas dalam satu tampilan.")
    ev = build_events()
    if ev.empty:
        empty_note("Belum ada agenda.")
        return
    c1, c2, c3 = st.columns([1.2, 1.2, 2])
    horizon = c1.selectbox("Rentang ke depan", [7, 14, 30, 60, 90, 180, 365],
                           index=[7, 14, 30, 60, 90, 180, 365].index(int(S("horizon_days"))) if int(S("horizon_days")) in [7, 14, 30, 60, 90, 180, 365] else 3,
                           format_func=lambda x: f"{x} hari", key="ag_h")
    past = c2.selectbox("Sertakan yang sudah lewat", [0, 7, 30, 90], index=1, format_func=lambda x: "Tidak" if x == 0 else f"{x} hari terakhir", key="ag_p")
    kats = sorted(ev["kategori"].unique().tolist())
    ksel = c3.multiselect("Kategori", kats, default=kats, key="ag_k")
    lo, hi = TODAY - timedelta(days=int(past)), TODAY + timedelta(days=int(horizon))
    f = ev[(ev["tanggal"] >= lo) & (ev["tanggal"] <= hi) & (ev["kategori"].isin(ksel))].copy()
    st.caption(f"**{len(f)}** agenda antara {fmt_date(lo)} s.d. {fmt_date(hi)}.")
    if f.empty:
        st.success("Tidak ada agenda pada rentang ini. ✅")
        return
    f["selisih"] = f["tanggal"].map(lambda d: (d - TODAY).days)
    out = pd.DataFrame({
        "Tanggal": f["tanggal"].map(lambda d: f"{HARI[d.weekday()][:3]}, {d:%d/%m/%Y}"),
        "H-": f["selisih"].map(lambda n: "HARI INI" if n == 0 else (f"H-{n}" if n > 0 else f"Lewat {abs(n)} hr")),
        "Kategori": f["kategori"], "Keterangan": f["keterangan"], "PJ": f["pj"], "Modul": f["modul"]})
    st.dataframe(out, hide_index=True, **WD)
    a, b, _ = st.columns([1, 1, 4])
    a.download_button("⬇️ Kalender (.ics)", data=make_ics(f), file_name=f"agenda_simkerma_{TODAY}.ics",
                      mime="text/calendar", key="ics", **WB)
    b.download_button("⬇️ Excel", data=to_excel_bytes({"Agenda": out}), file_name=f"agenda_{TODAY}.xlsx", key="ag_xls", **WB)
    f["tgl_dt"] = pd.to_datetime(f["tanggal"])
    fig = px.scatter(f, x="tgl_dt", y="kategori", color="kategori", hover_data={"keterangan": True, "tgl_dt": False})
    fig.update_traces(marker=dict(size=13, line=dict(width=1, color="#fff")))
    fig.update_xaxes(title="")
    fig.update_yaxes(title="")
    t0 = pd.Timestamp(TODAY).strftime("%Y-%m-%d")
    fig.add_shape(type="line", x0=t0, x1=t0, y0=0, y1=1, yref="paper", line=dict(color=BAD_C, width=2, dash="dash"))
    show_fig(fig, "Peta Agenda (garis merah = hari ini)", 120 + 45 * f["kategori"].nunique())


# =============================================================================
# 15. LAPORAN & EKSPOR
# =============================================================================
def page_laporan():
    page_header("📊 Laporan & Ekspor", "Rekapitulasi siap cetak/unduh untuk pelaporan ke pimpinan.")
    dok = enrich("dokumen", load("dokumen"))
    pra = enrich("praktik", load("praktik"))
    pen = enrich("penelitian", load("penelitian"))
    tug = enrich("tugas", load("tugas"))

    st.markdown("#### Rekap Dokumen Kerja Sama")
    if dok.empty:
        empty_note()
        rek_dok = pd.DataFrame()
    else:
        rek_dok = pd.crosstab(dok["jenis"], dok["masa_berlaku"], margins=True, margins_name="Total").reset_index()
        rek_dok = rek_dok.rename(columns={"jenis": "Jenis"})
        st.dataframe(rek_dok, hide_index=True, **WD)

    st.markdown("#### Rekap Praktik Mahasiswa per Institusi")
    if pra.empty:
        empty_note()
        rek_pra = pd.DataFrame()
    else:
        p = pra.copy()
        p["jumlah"] = pd.to_numeric(p["jumlah"], errors="coerce").fillna(0)
        p["retribusi"] = pd.to_numeric(p["retribusi"], errors="coerce").fillna(0)
        rek_pra = p.groupby("institusi").agg(Kegiatan=("id", "count"), Mahasiswa=("jumlah", "sum"),
                                             Retribusi=("retribusi", "sum")).reset_index()
        rek_pra = rek_pra.rename(columns={"institusi": "Institusi"}).sort_values("Mahasiswa", ascending=False)
        shown = rek_pra.copy()
        shown["Retribusi"] = shown["Retribusi"].map(rupiah)
        shown["Mahasiswa"] = shown["Mahasiswa"].astype(int)
        st.dataframe(shown, hide_index=True, **WD)

    st.markdown("#### Rekap Penelitian per Status")
    if pen.empty:
        empty_note()
        rek_pen = pd.DataFrame()
    else:
        rek_pen = pd.crosstab(pen["jenis"], pen["status"], margins=True, margins_name="Total").reset_index()
        rek_pen = rek_pen.rename(columns={"jenis": "Jenis"})
        st.dataframe(rek_pen, hide_index=True, **WD)

    st.markdown("#### Unduh Laporan Lengkap")
    sheets = {"Rekap Dokumen": rek_dok, "Rekap Praktik": rek_pra, "Rekap Penelitian": rek_pen,
              "Dokumen": full_export("dokumen") if not dok.empty else pd.DataFrame(),
              "Praktik": full_export("praktik") if not pra.empty else pd.DataFrame(),
              "Penelitian": full_export("penelitian") if not pen.empty else pd.DataFrame(),
              "Tindak Lanjut": full_export("tugas") if not tug.empty else pd.DataFrame()}
    st.download_button("⬇️ Unduh Excel Lengkap (semua modul)", data=to_excel_bytes(sheets),
                       file_name=f"laporan_simkerma_{TODAY}.xlsx", type="primary", key="rep_all", **WB)


# =============================================================================
# 16. PENGATURAN
# =============================================================================
def import_template(table: str) -> bytes:
    fields = TABLES[table]["fields"]
    tpl = pd.DataFrame(columns=[f["label"] for f in fields])
    notes = pd.DataFrame({
        "Kolom": [f["label"] for f in fields],
        "Wajib": ["Ya" if f.get("req") else "Tidak" for f in fields],
        "Tipe": [{"text": "Teks", "textarea": "Teks panjang", "select": "Pilihan", "date": "Tanggal (dd/mm/yyyy)",
                  "int": "Angka bulat", "number": "Angka", "bool": "Ya/Tidak", "url": "URL (https://…)",
                  "ref_dokumen": "Teks bebas"}.get(f["type"], "Teks") for f in fields],
        "Pilihan yang valid": [", ".join(MASTER.get(f["opts"], [])) if f["type"] == "select" else "" for f in fields]})
    return to_excel_bytes({"Data": tpl, "Petunjuk": notes}, keep_empty=True)


def import_rows(table: str, raw: pd.DataFrame) -> tuple[int, list[str]]:
    fields = TABLES[table]["fields"]
    lookup = {}
    for f in fields:
        for name in (f["label"], f["key"], f.get("short", "")):
            if name:
                lookup[norm(name)] = f
    mapping = {col: lookup[norm(col)] for col in raw.columns if norm(col) in lookup}
    if not mapping:
        return 0, ["Tidak ada kolom yang cocok. Gunakan template yang disediakan."]
    ok_n, errors = 0, []
    for i, row in raw.iterrows():
        data = {}
        for col, f in mapping.items():
            v = clean(row[col])
            t = f["type"]
            if t == "date":
                dv = pd.to_datetime(v, dayfirst=True, errors="coerce") if v is not None else pd.NaT
                data[f["key"]] = None if pd.isna(dv) else dv.date()
            elif t == "bool":
                data[f["key"]] = truthy(v)
            elif t in ("int", "number"):
                nv = pd.to_numeric(v, errors="coerce")
                data[f["key"]] = 0 if pd.isna(nv) else nv
            elif t == "select":
                s = str(v or "").strip()
                match = {o.lower(): o for o in MASTER.get(f["opts"], [])}
                data[f["key"]] = match.get(s.lower(), s)
            else:
                data[f["key"]] = str(v or "").strip()
        for f in fields:
            if f["type"] == "select" and not data.get(f["key"]):
                data[f["key"]] = (MASTER.get(f["opts"]) or [""])[0] if f.get("opts") != "jenis_dokumen" else ""
        errs = validate(table, data)
        if errs:
            errors.append(f"Baris {i + 2}: " + "; ".join(e.replace("**", "") for e in errs))
            continue
        save_record(table, data)
        ok_n += 1
    return ok_n, errors


def make_demo():
    random.seed(11)
    mitra = [("Universitas Indonesia", "Perguruan Tinggi"), ("Politeknik Kesehatan Kemenkes Bandung", "Perguruan Tinggi"),
             ("RSUD Cibinong", "Rumah Sakit"), ("Universitas Pakuan", "Perguruan Tinggi"),
             ("IPB University", "Perguruan Tinggi"), ("Ikatan Dokter Indonesia Cabang Bogor", "Organisasi Profesi"),
             ("PPNI Kabupaten Bogor", "Organisasi Profesi"), ("Klinik Sehat Sentosa", "Klinik/Praktik Mandiri"),
             ("STIKes Bogor Husada", "Perguruan Tinggi"), ("Universitas Gadjah Mada", "Perguruan Tinggi"),
             ("Yayasan Peduli Sehat", "LSM/Yayasan"), ("Puskesmas Cibinong", "Puskesmas")]
    bidang = MASTER["bidang"]
    for i, (m, jm) in enumerate(mitra):
        jenis = ["MoU", "PKS", "KB"][i % 3]
        mulai = TODAY - timedelta(days=random.randint(60, 900))
        akhir = TODAY + timedelta(days=random.choice([-120, -20, 12, 25, 55, 80, 200, 400, 700, 900]))
        save_record("dokumen", {
            "jenis": jenis, "nomor": f"{random.randint(100, 999)}/{jenis}/SDMK/{mulai.year}", "nomor_mitra": f"{random.randint(1, 99)}/KS/{mulai.year}",
            "mitra": m, "jenis_mitra": jm, "bidang": bidang[i % len(bidang)], "induk": "", "tgl_ttd": mulai, "tgl_mulai": mulai,
            "tgl_berakhir": akhir, "tanpa_batas": False, "status": "Aktif", "pj": random.choice(["Ratna S.", "Budi H.", "Dewi A."]),
            "kontak_pj": "0812-0000-0000", "kontak_mitra": "-", "link": "", "judul": f"{jenis} {bidang[i % len(bidang)]} dengan {m}",
            "ruang_lingkup": "Pelaksanaan kerja sama sesuai bidang yang disepakati.", "catatan": "Data demo"})
    prodi = ["Keperawatan", "Kebidanan", "Gizi", "Kesehatan Masyarakat", "Farmasi", "Analis Kesehatan", "Kedokteran"]
    for i in range(10):
        mulai = TODAY + timedelta(days=random.randint(-90, 60))
        selesai = mulai + timedelta(days=random.randint(14, 60))
        save_record("praktik", {
            "institusi": random.choice(mitra[:5] + mitra[8:10])[0], "prodi": random.choice(prodi), "jenjang": random.choice(MASTER["jenjang"]),
            "jumlah": random.randint(3, 30), "lokasi": random.choice(["Puskesmas Cibinong", "RSUD Cibinong", "Dinkes", "Puskesmas Citeureup"]),
            "tgl_mulai": mulai, "tgl_selesai": selesai, "pembimbing_inst": "Dosen Pembimbing", "pembimbing_lahan": "Preseptor Lahan",
            "no_surat": f"{random.randint(100, 999)}/PM/{TODAY.year}", "dokumen_ref": "", "status": random.choice(["Pengajuan", "Disetujui", "Berlangsung", "Selesai"]),
            "berkas_lengkap": random.choice([True, False]), "evaluasi_diterima": random.choice([True, False]),
            "retribusi": random.choice([0, 500000, 1500000]), "pj": "Ratna S.", "kontak": "-", "link": "", "catatan": "Data demo"})
    jud = ["Analisis Beban Kerja Perawat", "Evaluasi Program Posyandu", "Faktor Risiko Stunting", "Kepuasan Pasien Puskesmas",
           "Kepatuhan Minum Obat TB", "Implementasi SDMK Digital", "Kompetensi Bidan Desa", "Pemanfaatan Telemedisin"]
    for i, j in enumerate(jud):
        mulai = TODAY + timedelta(days=random.randint(-120, 30))
        selesai = mulai + timedelta(days=random.randint(30, 120))
        save_record("penelitian", {
            "peneliti": random.choice(["Aulia Rahma", "Dr. Hendra", "Siti Nurjanah", "Rizky Pratama"]), "institusi": random.choice(mitra[:5])[0],
            "jenis": random.choice(MASTER["jenis_penelitian"]), "lokasi": "Wilayah Kerja", "no_surat": f"{random.randint(100, 999)}/Pen/{TODAY.year}",
            "no_etik": f"KEPK/{random.randint(100, 999)}", "tgl_pengajuan": mulai - timedelta(days=14), "tgl_mulai": mulai, "tgl_selesai": selesai,
            "status": random.choice(["Pengajuan", "Izin Terbit", "Berlangsung", "Selesai", "Laporan Diserahkan"]),
            "laporan_diserahkan": random.choice([True, False]), "tgl_laporan": None, "pj": "Budi H.", "kontak": "-", "link": "",
            "judul": j, "catatan": "Data demo"})
    tugas = ["Kirim surat perpanjangan PKS", "Verifikasi berkas praktik gelombang 2", "Tagih laporan hasil penelitian",
             "Rapat evaluasi kerja sama semester", "Perbarui database mitra"]
    for t in tugas:
        save_record("tugas", {"modul": random.choice(MASTER["modul_tugas"]), "referensi": "", "jatuh_tempo": TODAY + timedelta(days=random.randint(-5, 30)),
                              "prioritas": random.choice(MASTER["prioritas"]), "status": random.choice(["Belum Mulai", "Berjalan"]),
                              "pj": "Ratna S.", "judul": t, "deskripsi": "", "catatan": "Data demo"})


def page_pengaturan():
    page_header("⚙️ Pengaturan Lanjutan", "Atur identitas, tampilan, peringatan, master data, keamanan, dan backup.")
    show_flash()
    tabs = st.tabs(["🏢 Umum", "🎨 Tampilan", "⏰ Peringatan", "🗂️ Master Data", "🔐 Keamanan",
                    "💾 Backup & Restore", "📥 Impor Data", "🧪 Data Demo / Reset"])

    with tabs[0]:
        with st.form("set_umum"):
            a = st.text_input("Nama Aplikasi", value=str(S("app_name")))
            b = st.text_input("Nama Instansi", value=str(S("instansi")))
            c = st.text_input("Unit Kerja", value=str(S("unit")))
            d = st.text_input("PJ Default (otomatis terisi di form baru)", value=str(S("default_pj") or ""))
            e = st.number_input("Batas ukuran lampiran (MB)", min_value=1, max_value=25, value=int(S("max_upload_mb")), step=1)
            if st.form_submit_button("💾 Simpan", type="primary", **WB):
                for k, v in (("app_name", a.strip() or DEFAULTS["app_name"]), ("instansi", b.strip()), ("unit", c.strip()),
                             ("default_pj", d.strip()), ("max_upload_mb", int(e))):
                    set_setting(k, v)
                log_event("Ubah Pengaturan", "pengaturan", "umum")
                flash("success", "Pengaturan umum tersimpan.")
                st.rerun()

    with tabs[1]:
        names = list(PALETTES.keys())
        cur = S("palette") if S("palette") in names else names[0]
        sel = st.selectbox("Palet Warna", names, index=names.index(cur), key="pal_sel")
        p = PALETTES[sel]
        st.markdown("".join(f'<span class="sw" style="background:{p[k]}" title="{k}"></span>' for k in ("primary", "secondary", "accent", "soft", "bg")),
                    unsafe_allow_html=True)
        st.caption("Primer · Sekunder · Aksen · Lembut · Latar")
        if st.button("Terapkan Palet", type="primary", key="apply_pal"):
            set_setting("palette", sel)
            flash("success", f"Palet '{sel}' diterapkan.")
            st.rerun()

    with tabs[2]:
        with st.form("set_warn"):
            w = st.number_input("Dokumen dianggap 'Segera Berakhir' bila sisa ≤ (hari)", min_value=7, max_value=365, value=int(S("warn_days")), step=1)
            k = st.number_input("Dokumen dianggap 'Kritis' bila sisa ≤ (hari)", min_value=1, max_value=364, value=int(S("kritis_days")), step=1)
            h = st.number_input("Rentang default Agenda (hari ke depan)", min_value=7, max_value=365, value=int(S("horizon_days")), step=1)
            if st.form_submit_button("💾 Simpan", type="primary", **WB):
                if k >= w:
                    st.error("Batas 'Kritis' harus lebih kecil dari 'Segera Berakhir'.")
                else:
                    set_setting("warn_days", int(w))
                    set_setting("kritis_days", int(k))
                    set_setting("horizon_days", int(h))
                    flash("success", "Ambang peringatan tersimpan.")
                    st.rerun()

    with tabs[3]:
        st.caption("Satu pilihan per baris. Mengubah/menghapus pilihan tidak mengubah data lama yang sudah tersimpan.")
        with st.form("set_master"):
            newm = {}
            for key, label in MASTER_LABELS.items():
                newm[key] = st.text_area(label, value="\n".join(MASTER[key]), height=110, key=f"m_{key}")
            if st.form_submit_button("💾 Simpan Master Data", type="primary", **WB):
                out = {}
                for k, v in newm.items():
                    items = list(dict.fromkeys([x.strip() for x in v.splitlines() if x.strip()]))
                    out[k] = items or DEFAULT_MASTER[k]
                set_setting("master", out)
                log_event("Ubah Pengaturan", "pengaturan", "master")
                flash("success", "Master data tersimpan.")
                st.rerun()
        if st.button("↩️ Kembalikan master data ke bawaan", key="reset_master"):
            ex("DELETE FROM pengaturan WHERE kunci='master'")
            flash("success", "Master data dikembalikan ke bawaan.")
            st.rerun()

    with tabs[4]:
        st.markdown("Atur password agar dashboard tidak dapat dibuka sembarang orang. **Admin** = akses penuh, **Viewer** = hanya lihat.")
        has_admin = bool(get_secret("ADMIN_PASSWORD") or SET.get("auth_admin"))
        has_viewer = bool(get_secret("VIEWER_PASSWORD") or SET.get("auth_viewer"))
        st.info(f"Status: Password Admin **{'aktif' if has_admin else 'belum diatur'}** · Password Viewer **{'aktif' if has_viewer else 'belum diatur'}**")
        with st.form("set_auth"):
            a = st.text_input("Password Admin baru", type="password")
            v = st.text_input("Password Viewer baru (opsional)", type="password")
            if st.form_submit_button("💾 Simpan Password", type="primary", **WB):
                if not a and not v:
                    st.error("Isi minimal salah satu password.")
                elif v and not (a or has_admin):
                    st.error("Atur password Admin terlebih dahulu sebelum membuat password Viewer.")
                elif (a and len(a) < 6) or (v and len(v) < 6):
                    st.error("Password minimal 6 karakter.")
                else:
                    salt = str(SET.get("auth_salt") or pysecrets.token_hex(8))
                    set_setting("auth_salt", salt)
                    if a:
                        set_setting("auth_admin", hash_pw(a, salt))
                        st.session_state["role"] = "admin"
                    if v:
                        set_setting("auth_viewer", hash_pw(v, salt))
                    log_event("Ubah Pengaturan", "pengaturan", "keamanan")
                    flash("success", "Password tersimpan.")
                    st.rerun()
        if st.button("🔓 Nonaktifkan semua password (dari menu ini)", key="auth_off"):
            ex("DELETE FROM pengaturan WHERE kunci IN ('auth_admin','auth_viewer','auth_salt')")
            flash("success", "Password dari menu ini dinonaktifkan. (Password di Secrets, bila ada, tetap berlaku.)")
            st.rerun()
        st.caption("Akses darurat: tambahkan `ADMIN_PASSWORD` di Secrets Streamlit — selalu berlaku sebagai Admin.")

    with tabs[5]:
        st.warning("⚠️ Di Streamlit Community Cloud, file database dapat ter-reset saat aplikasi di-restart/redeploy. "
                   "Aktifkan **Backup GitHub** di bawah atau unduh backup secara rutin.")
        a, b = st.columns(2)
        if a.button("📦 Siapkan Backup Database (.db)", key="prep_db", **WB):
            st.session_state["bk_db_bytes"] = db_bytes()
        if st.session_state.get("bk_db_bytes"):
            a.download_button("⬇️ Unduh Backup Database (.db)", data=st.session_state["bk_db_bytes"],
                              file_name=f"backup_simkerma_{TODAY}.db", key="bk_db", **WB)
        if b.button("📦 Siapkan Semua Data (Excel)", key="prep_xls", **WB):
            st.session_state["bk_xls_bytes"] = to_excel_bytes({t: full_export(t) for t in TABLES})
        if st.session_state.get("bk_xls_bytes"):
            b.download_button("⬇️ Unduh Semua Data (Excel)", data=st.session_state["bk_xls_bytes"],
                              file_name=f"data_simkerma_{TODAY}.xlsx", key="bk_xls", **WB)
        st.markdown("##### Pulihkan dari file backup (.db)")
        up = st.file_uploader("Pilih file .db", type=["db", "sqlite", "sqlite3"], key="restore_up")
        if up is not None:
            sure = st.checkbox("Saya paham data saat ini akan DITIMPA oleh file backup", key="restore_sure")
            if st.button("♻️ Pulihkan Database", disabled=not sure, key="restore_go"):
                ok, msg = restore_db(up.getvalue())
                log_event("Restore", "sistem", "", msg)
                flash("success" if ok else "error", msg)
                st.rerun()
        st.markdown("##### ☁️ Backup Otomatis ke GitHub")
        cfg = gh_cfg()
        if not cfg:
            st.info("Belum terhubung. Tambahkan ke **Secrets** (lihat contoh di bawah), gunakan repository **PRIVATE**.")
        else:
            st.success(f"Terhubung ke repo `{cfg['repo']}` (branch `{cfg['branch']}`, file `{cfg['path']}`).")
            with st.form("set_gh"):
                ab = st.checkbox("Aktifkan backup otomatis setelah ada perubahan data", value=bool(S("auto_backup")))
                iv = st.number_input("Jeda minimal antar backup (menit)", min_value=1, max_value=1440, value=int(S("backup_interval_min")), step=1)
                if st.form_submit_button("💾 Simpan", type="primary", **WB):
                    set_setting("auto_backup", bool(ab), track=False)
                    set_setting("backup_interval_min", int(iv), track=False)
                    flash("success", "Pengaturan backup tersimpan.")
                    st.rerun()
            x, y = st.columns(2)
            if x.button("☁️ Backup ke GitHub sekarang", key="gh_b", **WB):
                ok, msg = gh_backup()
                st.success(msg) if ok else st.error(msg)
            if y.button("⬇️ Restore dari GitHub", key="gh_r", **WB):
                ok, msg = gh_restore()
                if ok:
                    flash("success", msg)
                    st.rerun()
                else:
                    st.error(msg)
        with st.expander("Contoh isi Secrets (Streamlit Cloud → App settings → Secrets)"):
            st.code('ADMIN_PASSWORD = "ganti-dengan-password-kuat"\n'
                    'VIEWER_PASSWORD = "password-untuk-lihat-saja"\n\n'
                    'GITHUB_TOKEN = "github_pat_xxxxxxxxxxxx"   # fine-grained token, izin Contents: Read & Write\n'
                    'GITHUB_REPO = "usernameanda/repo-backup-privat"\n'
                    'GITHUB_BRANCH = "main"\n'
                    'GITHUB_BACKUP_PATH = "backup/kerjasama_sdmk.db"', language="toml")

    with tabs[6]:
        st.caption("Daftarkan banyak data sekaligus dari Excel/CSV. Unduh template, isi, lalu unggah.")
        tbl = st.selectbox("Modul tujuan", list(TABLES.keys()), format_func=lambda t: TABLES[t]["title"], key="imp_tbl")
        st.download_button("⬇️ Unduh Template Excel", data=import_template(tbl), file_name=f"template_{tbl}.xlsx", key="imp_tpl", **WB)
        up = st.file_uploader("Unggah file (.xlsx / .csv)", type=["xlsx", "csv"], key=f"imp_up_{tbl}")
        if up is not None:
            try:
                raw = pd.read_csv(up) if up.name.lower().endswith(".csv") else pd.read_excel(up, sheet_name=0)
                raw = raw.dropna(how="all")
                st.write(f"Terbaca **{len(raw)}** baris. Pratinjau:")
                st.dataframe(raw.head(10), hide_index=True, **WD)
                if st.button("📥 Impor Sekarang", type="primary", key="imp_go"):
                    n, errs = import_rows(tbl, raw)
                    log_event("Impor", tbl, "", f"{n} baris berhasil, {len(errs)} gagal")
                    if n:
                        st.success(f"{n} baris berhasil diimpor.")
                    if errs:
                        st.warning(f"{len(errs)} baris dilewati:")
                        st.code("\n".join(errs[:30]))
            except Exception as e:
                st.error(f"File tidak dapat dibaca: {e}")

    with tabs[7]:
        st.markdown("##### Data Demo")
        st.caption("Isi contoh data untuk mencoba semua fitur. Dapat dihapus lewat menu reset di bawah.")
        if st.button("🧪 Muat Data Demo", key="demo_go"):
            make_demo()
            log_event("Data Demo", "sistem", "", "dimuat")
            flash("success", "Data demo berhasil dimuat.")
            st.rerun()
        st.markdown("##### ⚠️ Reset Seluruh Data")
        st.caption("Menghapus semua dokumen, praktik, penelitian, tugas, dan lampiran. Pengaturan tetap dipertahankan.")
        txt = st.text_input("Ketik HAPUS SEMUA untuk konfirmasi", key="reset_txt")
        if st.button("🗑️ Hapus Semua Data", disabled=(txt.strip() != "HAPUS SEMUA"), key="reset_go"):
            for t in list(TABLES.keys()) + ["lampiran"]:
                ex(f"DELETE FROM {t}")
            log_event("Reset", "sistem", "", "semua data dihapus")
            flash("success", "Seluruh data dihapus.")
            st.rerun()


def page_log():
    page_header("🧾 Log Aktivitas", "Jejak perubahan data (1.000 aktivitas terakhir).")
    df = q("SELECT waktu AS Waktu, pengguna AS Pengguna, aksi AS Aksi, modul AS Modul, ref AS Ref, detail AS Detail "
           "FROM aktivitas ORDER BY id DESC LIMIT 1000")
    if df.empty:
        empty_note("Belum ada aktivitas.")
        return
    c1, c2 = st.columns([1, 2])
    mod = c1.multiselect("Modul", sorted(df["Modul"].dropna().unique().tolist()), key="log_m")
    s = c2.text_input("Cari", key="log_s")
    if mod:
        df = df[df["Modul"].isin(mod)]
    if s.strip():
        df = df[df.astype(str).agg(" ".join, axis=1).str.lower().str.contains(s.strip().lower(), regex=False)]
    st.dataframe(df, hide_index=True, **WD)
    st.download_button("⬇️ Unduh CSV", data=csv_bytes(df), file_name=f"log_{TODAY}.csv", key="log_dl", **WB)


# =============================================================================
# 17. MAIN
# =============================================================================
MENU = [
    ("home", "🏠 Beranda"), ("dokumen", "🤝 MoU · PKS · KB"), ("praktik", "🎓 Praktik Mahasiswa"),
    ("penelitian", "🔬 Penelitian"), ("tugas", "✅ Tindak Lanjut"), ("agenda", "📅 Agenda & Pengingat"),
    ("laporan", "📊 Laporan & Ekspor"), ("pengaturan", "⚙️ Pengaturan"), ("log", "🧾 Log Aktivitas"),
]
ADMIN_ONLY = {"pengaturan", "log"}


def main():
    inject_css()

    if "boot_done" not in st.session_state:
        st.session_state["boot_done"] = True
        msg = boot_restore_once("v1")
        if msg:
            st.session_state["flash"] = ("success", f"☁️ {msg} (dipulihkan otomatis dari GitHub)")
            st.rerun()

    if auth_required():
        if "role" not in st.session_state:
            login_screen()
            return
    else:
        st.session_state["role"] = "admin"
    role = st.session_state.get("role", "admin")

    items = [m for m in MENU if role == "admin" or m[0] not in ADMIN_ONLY]
    keys = [m[0] for m in items]
    names = dict(items)
    if st.session_state.get("menu") not in keys:
        st.session_state["menu"] = "home"

    with st.sidebar:
        st.markdown(f'<div class="side-brand">🏛️ {html.escape(str(S("app_name")))}</div>'
                    f'<div class="side-sub">{html.escape(str(S("instansi")))}<br>{html.escape(str(S("unit")))}</div>',
                    unsafe_allow_html=True)
        st.divider()
        page = st.radio("Menu", keys, format_func=lambda k: names[k], key="menu", label_visibility="collapsed")
        st.divider()
        st.markdown(f"👤 Peran: **{'Admin' if role == 'admin' else 'Viewer (hanya lihat)'}**")
        if auth_required() and st.button("Keluar", key="logout", **WB):
            st.session_state.pop("role", None)
            st.rerun()
        st.markdown(f'<div class="side-sub">v{APP_VERSION} · {TODAY:%d/%m/%Y}</div>', unsafe_allow_html=True)

    if page == "home":
        page_home()
    elif page == "dokumen":
        page_module("dokumen", "🤝", "Pencatatan dan pemantauan masa berlaku MoU, Perjanjian Kerja Sama (PKS), dan Kesepakatan Bersama (KB).")
    elif page == "praktik":
        page_module("praktik", "🎓", "Pengajuan, persetujuan, jadwal, dan evaluasi praktik mahasiswa di fasilitas kesehatan.")
    elif page == "penelitian":
        page_module("penelitian", "🔬", "Perizinan, jadwal, kelaikan etik, dan penyerahan laporan hasil penelitian.")
    elif page == "tugas":
        page_module("tugas", "✅", "Daftar tindak lanjut PJ SDMK: perpanjangan dokumen, verifikasi berkas, penagihan laporan, dll.")
    elif page == "agenda":
        page_agenda()
    elif page == "laporan":
        page_laporan()
    elif page == "pengaturan" and role == "admin":
        page_pengaturan()
    elif page == "log" and role == "admin":
        page_log()

    maybe_autobackup()


try:
    main()
except Exception as _err:  # jaring pengaman terakhir agar aplikasi tidak "crash putih"
    if type(_err).__name__ in ("RerunException", "StopException", "RerunData"):
        raise
    st.error("Terjadi kesalahan tak terduga. Muat ulang halaman; bila berulang, hubungi admin sistem.")
    st.exception(_err)
