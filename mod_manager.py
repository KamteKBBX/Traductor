#!/usr/bin/env python3
# mod_manager.py — Gestor integral del catálogo de mods del KamteK Launcher
# ---------------------------------------------------------------------------
# v1.3.1
# ---------------------------------------------------------------------------
# Cubre TODO lo relacionado con mods_config.json:
#   • Organizar  → buscar, reordenar mods (arriba/abajo/inicio/final)
#   • Modificar  → editar cualquier campo del mod y de cada parte/complemento
#   • Añadir     → nuevo mod, nueva parte, nuevo complemento
#   • Eliminar   → mod, parte o complemento
#   • Reformatear la indentación (indent=4, UTF-8, LF)
#   • Validación completa antes de cada guardado
#   • Guardado ATÓMICO con verificación (temp → hash → os.replace)
#   • Backup automático (.bak + backups/<fecha>.json)
#   • Sugerencias automáticas de categorías / juegos / motores / autores / trads
#   • Auto-detección de tipo y extracción de ID desde URLs (GDrive / ModDB / direct)
#   • Saneamiento de ids al cargar, al editar y al guardar
#   • LOG DE ERRORES a archivo (mod_manager_log.txt) con traceback completo
#
# Uso:
#   python mod_manager.py                   # Interfaz gráfica (por defecto)
#   python mod_manager.py --cli             # Asistente por consola (heredado)
#   python mod_manager.py --check           # Validar + reformatear sin abrir GUI
#   python mod_manager.py --file RUTA.json  # Ruta alternativa al catálogo
# ---------------------------------------------------------------------------

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog


VERSION_MANAGER = "1.3.1"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE_DEFAULT = os.path.join(SCRIPT_DIR, "mods_config.json")
LOG_FILE_MANAGER    = os.path.join(SCRIPT_DIR, "mod_manager_log.txt")
BACKUP_DIR_NAME     = "backups"

INVALID_CHARS = r'<>:"/\|?*'
VALID_TYPES = ("gdrive", "moddb", "direct")


# ============================================================
# Logging a archivo (v1.3.1)
# ============================================================
def log_error(mensaje, exc=None):
    """Escribe un mensaje de error con timestamp. Si exc es una tupla
    (type, value, tb), añade el traceback formateado."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE_MANAGER, 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] {mensaje}\n")
            if exc is not None:
                try:
                    tb_str = ''.join(traceback.format_exception(*exc))
                    f.write(tb_str + "\n")
                except Exception:
                    pass
    except Exception:
        pass


def log_info(mensaje):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE_MANAGER, 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] [INFO] {mensaje}\n")
    except Exception:
        pass


# ============================================================
# Utilidades compartidas
# ============================================================
def sanitizar_nombre_archivo(nombre: str) -> str:
    for c in INVALID_CHARS:
        nombre = nombre.replace(c, '_')
    return nombre


def derivar_folder(name: str) -> str:
    folder = sanitizar_nombre_archivo((name or "mod").strip())
    return folder[:50].rstrip('. ') or "mod"


def derivar_id(name: str) -> str:
    base = (name or "mod").lower()
    base = ''.join(c if c.isalnum() else '_' for c in base).strip('_')
    return base or "mod"


def id_unico(mods, base: str, excluir_idx=None) -> str:
    usados = {m.get('id', '') for i, m in enumerate(mods) if i != excluir_idx}
    base = base or "mod"
    if base and base not in usados:
        return base
    n = 2
    while f"{base}_{n}" in usados:
        n += 1
    return f"{base}_{n}"


# ============================================================
# Extracción de IDs desde URLs
# ============================================================
def detectar_tipo_desde_url(texto) -> str:
    if not texto:
        return None
    t = str(texto).strip()
    if not t:
        return None
    if 'drive.google.com' in t or 'docs.google.com' in t:
        return 'gdrive'
    if 'moddb.com' in t:
        return 'moddb'
    if t.startswith(('http://', 'https://', 'ftp://')):
        return 'direct'
    return None


def extraer_id_gdrive(texto) -> str:
    if not texto:
        return texto
    t = str(texto).strip()
    if '://' not in t:
        return t
    m = re.search(r'/file/d/([a-zA-Z0-9_-]+)', t)
    if m:
        return m.group(1)
    m = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', t)
    if m:
        return m.group(1)
    return t


def extraer_id_moddb(texto) -> str:
    if not texto:
        return texto
    t = str(texto).strip()
    if '://' not in t:
        return t
    m = re.search(r'/downloads/start/(\d+)', t)
    if m:
        return m.group(1)
    m = re.search(r'/downloads/[^/?#\s]+\.(\d+)(?=[/?#]|$)', t)
    if m:
        return m.group(1)
    return t


def extraer_id_segun_tipo(texto, tipo) -> str:
    if tipo == 'gdrive':
        return extraer_id_gdrive(texto)
    if tipo == 'moddb':
        return extraer_id_moddb(texto)
    return texto


def parece_url(valor) -> bool:
    if not isinstance(valor, str):
        return False
    return '://' in valor


def auto_extraer_ids_de_parte(p):
    """Extrae ID si 'id' contiene una URL. Devuelve (parte, cambio_bool)."""
    if not isinstance(p, dict):
        return p, False
    tipo = p.get('type', '')
    if tipo in ('gdrive', 'moddb'):
        old_id = p.get('id', '')
        if parece_url(old_id):
            new_id = extraer_id_segun_tipo(old_id, tipo)
            if new_id and new_id != old_id:
                p['id'] = new_id
                return p, True
    return p, False


def auto_fix_partes(mod):
    """Recorre parts y complements del mod y extrae IDs desde URLs.
    Devuelve el número de correcciones aplicadas."""
    if not isinstance(mod, dict):
        return 0
    cambios = 0
    for key in ('parts', 'complements'):
        items = mod.get(key) or []
        for p in items:
            _, cambio = auto_extraer_ids_de_parte(p)
            if cambio:
                cambios += 1
    return cambios


# ============================================================
# Serialización + validación
# ============================================================
def serializar(mods) -> str:
    return json.dumps(mods, indent=4, ensure_ascii=False)


def normalizar_parte(p):
    p = dict(p) if isinstance(p, dict) else {}
    if p.get('type') not in VALID_TYPES:
        if 'url' in p:
            p['type'] = 'direct'
        elif 'id' in p:
            p['type'] = 'gdrive'
        else:
            p['type'] = 'direct'
    return p


def normalizar_mod(m, idx=0):
    m = dict(m) if isinstance(m, dict) else {}
    m.setdefault('name', f'mod_{idx}')
    if not m.get('id'):
        m['id'] = derivar_id(m.get('name', f'mod_{idx}'))
    if not m.get('mod_folder'):
        m['mod_folder'] = derivar_folder(m.get('name', ''))
    m.setdefault('version', '1.0')
    m.setdefault('autor', '')
    m.setdefault('trad', '')
    m.setdefault('sinopsis', '')
    m.setdefault('descripcion_larga', '')
    m.setdefault('categoria', 'Sin clasificar')
    m.setdefault('juego_base', 'Desconocido')
    m.setdefault('motor', 'Desconocido')
    m['parts'] = [normalizar_parte(p) for p in m.get('parts') or []]
    m['complements'] = [normalizar_parte(p) for p in m.get('complements') or []]
    return m


def normalizar_todos(mods):
    out = []
    usados = set()
    for i, m in enumerate(mods or []):
        nm = normalizar_mod(m, i)
        if nm['id'] in usados:
            nm['id'] = id_unico([{'id': u} for u in usados], nm['id'])
        usados.add(nm['id'])
        out.append(nm)
    return out


def validar_catalogo(mods) -> list:
    problemas = []
    if not isinstance(mods, list):
        return ["El catálogo no es una lista."]

    ids_vistos = {}
    for i, m in enumerate(mods):
        if not isinstance(m, dict):
            problemas.append(f"Mod #{i}: no es un objeto.")
            continue
        mid = m.get('id', '')
        if not mid:
            problemas.append(f"Mod #{i} ({m.get('name', '?')}): falta 'id'.")
        elif mid in ids_vistos:
            problemas.append(f"Mod #{i}: 'id' duplicado {mid!r} (también en #{ids_vistos[mid]}).")
        else:
            ids_vistos[mid] = i

        if not m.get('name'):
            problemas.append(f"Mod #{i}: falta 'name'.")
        if not m.get('mod_folder'):
            problemas.append(f"Mod #{i} ({m.get('name', '?')}): falta 'mod_folder'.")

        for key in ('parts', 'complements'):
            items = m.get(key, [])
            if not isinstance(items, list):
                problemas.append(f"Mod #{i}: '{key}' no es lista.")
                continue
            for j, p in enumerate(items):
                if not isinstance(p, dict):
                    problemas.append(f"Mod #{i}.{key}[{j}]: no es objeto.")
                    continue
                if not p.get('file_name'):
                    problemas.append(f"Mod #{i}.{key}[{j}]: falta 'file_name'.")
                tipo = p.get('type', '')
                if tipo not in VALID_TYPES:
                    problemas.append(f"Mod #{i}.{key}[{j}]: tipo inválido {tipo!r}.")
                elif tipo == 'direct':
                    if not p.get('url'):
                        problemas.append(f"Mod #{i}.{key}[{j}]: falta 'url' (tipo direct).")
                else:
                    pid = p.get('id', '')
                    if not pid:
                        problemas.append(f"Mod #{i}.{key}[{j}]: falta 'id'.")
                    elif parece_url(pid):
                        problemas.append(
                            f"Mod #{i}.{key}[{j}]: 'id' parece una URL "
                            f"({pid[:60]}{'…' if len(str(pid)) > 60 else ''}). "
                            f"Debe contener solo el ID.")

    return problemas


# ============================================================
# Persistencia atómica + backup verificado
# ============================================================
def guardar_atomico(mods, path, hacer_backup=True, validar=True):
    if validar:
        problemas = validar_catalogo(mods)
        if problemas:
            raise ValueError("Validación fallida:\n" + "\n".join(f"  • {p}" for p in problemas))

    texto = serializar(mods)
    json.loads(texto)

    dir_destino = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_destino, exist_ok=True)

    backup_path = None
    if hacer_backup and os.path.exists(path):
        backup_dir = os.path.join(dir_destino, BACKUP_DIR_NAME)
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base = os.path.splitext(os.path.basename(path))[0]
        backup_path = os.path.join(backup_dir, f"{base}_{ts}.json")
        shutil.copy2(path, backup_path)
        try:
            shutil.copy2(path, path + ".bak")
        except Exception:
            pass

    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_mods_", suffix=".json", dir=dir_destino)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())

        with open(tmp_path, 'rb') as f:
            hash_disco = hashlib.md5(f.read()).hexdigest()
        hash_esperado = hashlib.md5(texto.encode('utf-8')).hexdigest()
        if hash_disco != hash_esperado:
            raise IOError("Verificación fallida: hash del archivo temporal no coincide.")

        with open(tmp_path, 'r', encoding='utf-8') as f:
            re_parseado = json.load(f)
        if not isinstance(re_parseado, list) or len(re_parseado) != len(mods):
            raise IOError("Verificación fallida: contenido re-parseado no coincide.")

        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise

    return backup_path


def cargar_catalogo(path):
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError(f"Contenido inesperado en {path}: se esperaba lista.")
    return data


# ============================================================
# Diálogo para editar un archivo (parte / complemento) — v1.3.1
# ============================================================
class PartDialog(tk.Toplevel):
    def __init__(self, parent, parte=None):
        super().__init__(parent)
        self.title("Editar archivo" if parte else "Nuevo archivo")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        self.resultado = None
        self._orig = dict(parte) if parte else {}

        self.var_file = tk.StringVar(value=self._orig.get('file_name', ''))
        self.var_type = tk.StringVar(value=self._orig.get('type', 'gdrive'))
        tipo = self.var_type.get()
        loc = self._orig.get('url', '') if tipo == 'direct' else self._orig.get('id', '')
        self.var_loc = tk.StringVar(value=loc)

        f = ttk.Frame(self, padding=14)
        f.pack(fill=tk.BOTH, expand=True)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Nombre del archivo *").grid(row=0, column=0, sticky='w', pady=4)
        ttk.Entry(f, textvariable=self.var_file, width=54).grid(row=0, column=1, sticky='ew', pady=4)

        ttk.Label(f, text="Tipo *").grid(row=1, column=0, sticky='w', pady=4)
        ttk.Combobox(f, textvariable=self.var_type, values=list(VALID_TYPES),
                     state='readonly', width=12).grid(row=1, column=1, sticky='w', pady=4)

        self.lbl_loc = ttk.Label(f, text="ID *")
        self.lbl_loc.grid(row=2, column=0, sticky='w', pady=4)
        self.entry_loc = ttk.Entry(f, textvariable=self.var_loc, width=54)
        self.entry_loc.grid(row=2, column=1, sticky='ew', pady=4)

        self.lbl_hint = ttk.Label(f, text="", foreground="#666", wraplength=460, justify='left')
        self.lbl_hint.grid(row=3, column=0, columnspan=2, sticky='w', pady=(0, 10))

        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, columnspan=2, sticky='e')
        ttk.Button(btns, text="Cancelar", command=self._cancelar).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btns, text="Aceptar", command=self._aceptar).pack(side=tk.RIGHT, padx=2)

        self.var_type.trace_add('write', lambda *_: self._actualizar_etiqueta())
        self.entry_loc.bind('<FocusOut>', self._on_loc_focus_out)
        self.entry_loc.bind('<Return>',   lambda e: self._aceptar())

        self._actualizar_etiqueta()
        # Si la parte viene con URL, extraer YA en la apertura
        if parece_url(self.var_loc.get()):
            self._auto_extraer()
            log_info(f"PartDialog: extracción inicial aplicada "
                     f"({self.var_file.get()!r} → id={self.var_loc.get()!r})")

        self.bind('<Escape>', lambda e: self._cancelar())
        self.wait_visibility()
        self.focus_set()

    def _actualizar_etiqueta(self):
        tipo = self.var_type.get()
        if tipo == 'direct':
            self.lbl_loc.config(text="URL *")
            self.lbl_hint.config(text="URL directa (http/https).", foreground="#666")
        elif tipo == 'gdrive':
            self.lbl_loc.config(text="Google Drive ID *")
            self.lbl_hint.config(
                text="Pega la URL completa o el ID. Se extrae automáticamente.",
                foreground="#666")
        else:
            self.lbl_loc.config(text="ModDB ID *")
            self.lbl_hint.config(
                text="Pega la URL de /downloads/start/… o el ID numérico. Se extrae automáticamente.",
                foreground="#666")

    def _on_loc_focus_out(self, _event=None):
        self._auto_extraer()

    def _auto_extraer(self):
        loc = self.var_loc.get().strip()
        if not loc:
            self._actualizar_etiqueta()
            return

        tipo_det = detectar_tipo_desde_url(loc)
        cambio_tipo = False
        if tipo_det and tipo_det != self.var_type.get():
            self.var_type.set(tipo_det)
            cambio_tipo = True

        tipo = self.var_type.get()
        nuevo = extraer_id_segun_tipo(loc, tipo)

        if nuevo != loc:
            self.var_loc.set(nuevo)
            msg = f"✓ ID extraído: {nuevo}"
            if cambio_tipo:
                msg += f"  (tipo → {tipo})"
            self.lbl_hint.config(text=msg, foreground='#0a7d00')
            log_info(f"PartDialog: auto-extracción {loc[:60]!r} → {nuevo!r} (tipo={tipo})")
        elif cambio_tipo:
            self.lbl_hint.config(text=f"✓ Tipo cambiado a {tipo}", foreground='#0a7d00')
        else:
            self._actualizar_etiqueta()

    def _aceptar(self):
        try:
            self._auto_extraer()
            file_name = self.var_file.get().strip()
            tipo = self.var_type.get()
            loc = self.var_loc.get().strip()
            if not file_name:
                messagebox.showwarning("Falta", "El nombre del archivo es obligatorio.", parent=self)
                return
            if not loc:
                messagebox.showwarning("Falta", "El campo ID/URL es obligatorio.", parent=self)
                return

            parte = dict(self._orig)
            parte['file_name'] = file_name
            parte['type'] = tipo
            if tipo == 'direct':
                parte['url'] = loc
                parte.pop('id', None)
            else:
                parte['id'] = loc
                parte.pop('url', None)
            self.resultado = parte
            self.destroy()
        except Exception as e:
            log_error(f"Error en PartDialog._aceptar: {e}", exc=sys.exc_info())
            messagebox.showerror("Error inesperado",
                f"{e}\n\nDetalles en:\n{LOG_FILE_MANAGER}", parent=self)

    def _cancelar(self):
        self.resultado = None
        self.destroy()


# ============================================================
# Ventana principal — v1.3.1
# ============================================================
class ModManagerApp:
    def __init__(self, root, config_path):
        self.root = root
        self.config_file = config_path

        self.mods = []
        self.mods_filtrados = []
        self.idx_actual = None
        self.dirty = False
        self._cargando = False
        self._suprimir = False
        self._ultimo_folder_derivado = ""

        # Captura de excepciones en callbacks de Tkinter
        self.root.report_callback_exception = self._report_callback_exception

        self._crear_ui()
        self._cargar()
        self.root.protocol("WM_DELETE_WINDOW", self._al_cerrar)

    def _report_callback_exception(self, exc_type, exc_value, exc_tb):
        log_error(f"Error en callback de UI: {exc_type.__name__}: {exc_value}",
                  exc=(exc_type, exc_value, exc_tb))
        try:
            messagebox.showerror(
                "Error inesperado",
                f"{exc_type.__name__}: {exc_value}\n\n"
                f"Se registró el detalle completo en:\n{LOG_FILE_MANAGER}")
        except Exception:
            pass

    # ---------- Construcción UI ----------
    def _crear_ui(self):
        self.root.title(f"Gestor de Mods v{VERSION_MANAGER} — {os.path.basename(self.config_file)}")
        self.root.geometry("1220x800")
        self.root.minsize(1000, 620)
        try:
            ttk.Style().theme_use('clam')
        except Exception:
            pass

        self._crear_menu()
        self._crear_toolbar()

        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        self._crear_panel_lista(left)

        right = ttk.Frame(paned)
        paned.add(right, weight=4)
        self._crear_panel_editor(right)

        status = ttk.Frame(self.root, relief='sunken', padding=(6, 3))
        status.pack(fill=tk.X, side=tk.BOTTOM)
        self.var_status = tk.StringVar(value="Listo.")
        ttk.Label(status, textvariable=self.var_status).pack(side=tk.LEFT)
        self.var_dirty = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.var_dirty, foreground='#b00020',
                  font=('TkDefaultFont', 9, 'bold')).pack(side=tk.RIGHT)

        self.root.bind('<Control-n>', lambda e: self._nuevo_mod())
        self.root.bind('<Control-o>', lambda e: self._abrir())
        self.root.bind('<Control-s>', lambda e: self._guardar())
        self.root.bind('<Control-r>', lambda e: self._recargar())
        self.root.bind('<F5>',         lambda e: self._recargar())

    def _crear_menu(self):
        mb = tk.Menu(self.root)

        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Nuevo mod",       accelerator="Ctrl+N", command=self._nuevo_mod)
        m.add_command(label="Abrir…",          accelerator="Ctrl+O", command=self._abrir)
        m.add_command(label="Recargar",        accelerator="Ctrl+R", command=self._recargar)
        m.add_separator()
        m.add_command(label="Guardar",         accelerator="Ctrl+S", command=self._guardar)
        m.add_command(label="Reformatear JSON",                        command=self._reformatear)
        m.add_separator()
        m.add_command(label="Salir",                                  command=self._al_cerrar)
        mb.add_cascade(label="Archivo", menu=m)

        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Eliminar mod",  command=self._eliminar_mod)
        m.add_command(label="Duplicar mod",  command=self._duplicar_mod)
        m.add_separator()
        m.add_command(label="Validar catálogo", command=self._validar_ui)
        mb.add_cascade(label="Editar", menu=m)

        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Mover al inicio", accelerator="Ctrl+Home",
                      command=lambda: self._mover_mod_a(0))
        m.add_command(label="Mover arriba",   accelerator="Alt+↑",
                      command=lambda: self._mover_mod(-1))
        m.add_command(label="Mover abajo",    accelerator="Alt+↓",
                      command=lambda: self._mover_mod(1))
        m.add_command(label="Mover al final", accelerator="Ctrl+End",
                      command=lambda: self._mover_mod_a(len(self.mods) - 1))
        mb.add_cascade(label="Mod", menu=m)

        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Ver log de errores",
                      command=self._abrir_log)
        m.add_command(label="Acerca de…", command=self._acerca_de)
        mb.add_cascade(label="Ayuda", menu=m)

        self.root.config(menu=mb)

    def _crear_toolbar(self):
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="➕ Nuevo",     command=self._nuevo_mod).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="💾 Guardar",   command=self._guardar).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="📂 Recargar",  command=self._recargar).pack(side=tk.LEFT, padx=2)
        ttk.Separator(bar, orient='vertical').pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(bar, text="🗑️ Eliminar",  command=self._eliminar_mod).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="📑 Duplicar",  command=self._duplicar_mod).pack(side=tk.LEFT, padx=2)

        ttk.Separator(bar, orient='vertical').pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Label(bar, text="↕ Reordenar:").pack(side=tk.LEFT, padx=(0, 4))
        self.btn_mod_inicio = ttk.Button(bar, text="⇈ Inicio",
                                         command=lambda: self._mover_mod_a(0))
        self.btn_mod_arriba = ttk.Button(bar, text="↑ Subir",
                                         command=lambda: self._mover_mod(-1))
        self.btn_mod_abajo  = ttk.Button(bar, text="↓ Bajar",
                                         command=lambda: self._mover_mod(1))
        self.btn_mod_final  = ttk.Button(bar, text="⇊ Final",
                                         command=lambda: self._mover_mod_a(len(self.mods) - 1))
        self.btn_mod_inicio.pack(side=tk.LEFT, padx=1)
        self.btn_mod_arriba.pack(side=tk.LEFT, padx=1)
        self.btn_mod_abajo.pack(side=tk.LEFT, padx=1)
        self.btn_mod_final.pack(side=tk.LEFT, padx=1)

        ttk.Separator(bar, orient='vertical').pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(bar, text="🔍 Validar",    command=self._validar_ui).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="🧹 Reformatear", command=self._reformatear).pack(side=tk.LEFT, padx=2)

    def _crear_panel_lista(self, parent):
        ttk.Label(parent, text="Mods en catálogo:",
                  font=('TkDefaultFont', 10, 'bold')).pack(anchor='w')

        sf = ttk.Frame(parent)
        sf.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(sf, text="🔍").pack(side=tk.LEFT)
        self.var_busqueda = tk.StringVar()
        self.var_busqueda.trace_add('write', lambda *_: self._actualizar_lista())
        ttk.Entry(sf, textvariable=self.var_busqueda).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        lf = ttk.Frame(parent)
        lf.pack(fill=tk.BOTH, expand=True)
        self.lista = tk.Listbox(lf, exportselection=False, activestyle='none')
        sb = ttk.Scrollbar(lf, orient='vertical', command=self.lista.yview)
        self.lista.config(yscrollcommand=sb.set)
        self.lista.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.lista.bind('<<ListboxSelect>>', self._on_seleccionar)
        self.lista.bind('<Alt-Up>',       self._on_key_mover_arriba)
        self.lista.bind('<Alt-Down>',     self._on_key_mover_abajo)
        self.lista.bind('<Control-Home>', self._on_key_mover_inicio)
        self.lista.bind('<Control-End>',  self._on_key_mover_final)

        self.lbl_contador = ttk.Label(parent, text="0 mods")
        self.lbl_contador.pack(anchor='w', pady=(4, 0))

        self.lbl_posicion = ttk.Label(parent, text="", foreground='#666',
                                      font=('TkDefaultFont', 9))
        self.lbl_posicion.pack(anchor='w', pady=(0, 2))

    def _crear_panel_editor(self, parent):
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_general     = ttk.Frame(self.notebook, padding=10)
        self.tab_parts       = ttk.Frame(self.notebook, padding=10)
        self.tab_complements = ttk.Frame(self.notebook, padding=10)
        self.tab_preview     = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(self.tab_general,     text="General")
        self.notebook.add(self.tab_parts,       text="Partes")
        self.notebook.add(self.tab_complements, text="Complementos")
        self.notebook.add(self.tab_preview,     text="Vista JSON")

        self._crear_tab_general()
        self._crear_tab_partes(self.tab_parts, 'parts')
        self._crear_tab_partes(self.tab_complements, 'complements')
        self._crear_tab_preview()

    def _crear_tab_general(self):
        f = self.tab_general
        f.columnconfigure(1, weight=1)
        f.columnconfigure(3, weight=1)

        self.var_id        = tk.StringVar()
        self.var_name      = tk.StringVar()
        self.var_version   = tk.StringVar()
        self.var_autor     = tk.StringVar()
        self.var_trad      = tk.StringVar()
        self.var_categoria = tk.StringVar()
        self.var_juego     = tk.StringVar()
        self.var_motor     = tk.StringVar()
        self.var_folder    = tk.StringVar()

        row = 0
        ttk.Label(f, text="ID *").grid(row=row, column=0, sticky='w', pady=2)
        ttk.Entry(f, textvariable=self.var_id).grid(row=row, column=1, sticky='ew', padx=(4, 16), pady=2)
        ttk.Label(f, text="Nombre *").grid(row=row, column=2, sticky='w', pady=2)
        ttk.Entry(f, textvariable=self.var_name).grid(row=row, column=3, sticky='ew', padx=(4, 0), pady=2)

        row += 1
        ttk.Label(f, text="Versión").grid(row=row, column=0, sticky='w', pady=2)
        ttk.Entry(f, textvariable=self.var_version).grid(row=row, column=1, sticky='ew', padx=(4, 16), pady=2)
        ttk.Label(f, text="Autor").grid(row=row, column=2, sticky='w', pady=2)
        self.cb_autor = ttk.Combobox(f, textvariable=self.var_autor)
        self.cb_autor.grid(row=row, column=3, sticky='ew', padx=(4, 0), pady=2)

        row += 1
        ttk.Label(f, text="Traductor").grid(row=row, column=0, sticky='w', pady=2)
        self.cb_trad = ttk.Combobox(f, textvariable=self.var_trad)
        self.cb_trad.grid(row=row, column=1, sticky='ew', padx=(4, 16), pady=2)
        ttk.Label(f, text="Categoría").grid(row=row, column=2, sticky='w', pady=2)
        self.cb_categoria = ttk.Combobox(f, textvariable=self.var_categoria)
        self.cb_categoria.grid(row=row, column=3, sticky='ew', padx=(4, 0), pady=2)

        row += 1
        ttk.Label(f, text="Juego base").grid(row=row, column=0, sticky='w', pady=2)
        self.cb_juego = ttk.Combobox(f, textvariable=self.var_juego)
        self.cb_juego.grid(row=row, column=1, sticky='ew', padx=(4, 16), pady=2)
        ttk.Label(f, text="Motor").grid(row=row, column=2, sticky='w', pady=2)
        self.cb_motor = ttk.Combobox(f, textvariable=self.var_motor)
        self.cb_motor.grid(row=row, column=3, sticky='ew', padx=(4, 0), pady=2)

        row += 1
        ttk.Label(f, text="Carpeta (mod_folder)").grid(row=row, column=0, sticky='w', pady=2)
        ttk.Entry(f, textvariable=self.var_folder).grid(row=row, column=1, columnspan=3,
                                                         sticky='ew', padx=(4, 0), pady=2)

        row += 1
        ttk.Label(f, text="Sinopsis").grid(row=row, column=0, sticky='nw', pady=(6, 2))
        self.txt_sinopsis = tk.Text(f, height=4, wrap='word')
        self.txt_sinopsis.grid(row=row, column=1, columnspan=3, sticky='nsew', padx=(4, 0), pady=(6, 2))

        row += 1
        ttk.Label(f, text="Descripción larga").grid(row=row, column=0, sticky='nw', pady=2)
        self.txt_desc = tk.Text(f, height=8, wrap='word')
        self.txt_desc.grid(row=row, column=1, columnspan=3, sticky='nsew', padx=(4, 0), pady=2)
        f.rowconfigure(row, weight=1)

        self.var_name.trace_add('write', self._on_name_cambio)
        for v in (self.var_id, self.var_name, self.var_version, self.var_autor,
                  self.var_trad, self.var_categoria, self.var_juego, self.var_motor, self.var_folder):
            v.trace_add('write', self._marcar_dirty)
        self.txt_sinopsis.bind('<<Modified>>', self._on_text_modified)
        self.txt_desc.bind('<<Modified>>',     self._on_text_modified)

    def _crear_tab_partes(self, parent, key):
        ttk.Label(parent,
                  text=f"{'Partes principales' if key == 'parts' else 'Complementos'}",
                  font=('TkDefaultFont', 10, 'bold')).pack(anchor='w')

        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        cols = ('file_name', 'type', 'location')
        tree = ttk.Treeview(frame, columns=cols, show='headings',
                            selectmode='browse', height=14)
        tree.heading('file_name', text='Archivo')
        tree.heading('type',      text='Tipo')
        tree.heading('location',  text='ID / URL')
        tree.column('file_name', width=320, anchor='w')
        tree.column('type',      width=80,  anchor='center', stretch=False)
        tree.column('location',  width=420, anchor='w')

        sb = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.config(yscrollcommand=sb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.bind('<Double-1>', lambda e, k=key: self._editar_parte(k))

        btns = ttk.Frame(parent)
        btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="➕ Añadir",   command=lambda: self._añadir_parte(key)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="✏️ Editar",   command=lambda: self._editar_parte(key)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="🗑️ Eliminar", command=lambda: self._eliminar_parte(key)).pack(side=tk.LEFT, padx=2)
        ttk.Separator(btns, orient='vertical').pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(btns, text="⬆️ Subir",    command=lambda: self._mover_parte(key, -1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="⬇️ Bajar",    command=lambda: self._mover_parte(key,  1)).pack(side=tk.LEFT, padx=2)

        if key == 'parts':
            self.tree_parts = tree
        else:
            self.tree_complements = tree

    def _crear_tab_preview(self):
        self.txt_preview = tk.Text(self.tab_preview, wrap='none', font=('Consolas', 9))
        sv = ttk.Scrollbar(self.tab_preview, orient='vertical',   command=self.txt_preview.yview)
        sh = ttk.Scrollbar(self.tab_preview, orient='horizontal', command=self.txt_preview.xview)
        self.txt_preview.config(yscrollcommand=sv.set, xscrollcommand=sh.set)
        self.txt_preview.grid(row=0, column=0, sticky='nsew')
        sv.grid(row=0, column=1, sticky='ns')
        sh.grid(row=1, column=0, sticky='ew')
        self.tab_preview.rowconfigure(0, weight=1)
        self.tab_preview.columnconfigure(0, weight=1)
        self.txt_preview.config(state='disabled')

    # ---------- Helpers ----------
    def _tree_de(self, key):
        return self.tree_parts if key == 'parts' else self.tree_complements

    def _set_status(self, msg):
        self.var_status.set(msg)

    def _actualizar_titulo(self):
        base = f"Gestor de Mods v{VERSION_MANAGER} — {os.path.basename(self.config_file)}"
        self.root.title(base + (" *" if self.dirty else ""))
        self.var_dirty.set("● Sin guardar" if self.dirty else "")

    def _refrescar_sugerencias(self):
        cats, juegos, motores, autores, trads = set(), set(), set(), set(), set()
        for m in self.mods:
            for campo, pool in (('categoria', cats), ('juego_base', juegos),
                                ('motor', motores), ('autor', autores), ('trad', trads)):
                v = (m.get(campo) or '').strip()
                if v:
                    pool.add(v)
        self.cb_categoria['values'] = sorted(cats)
        self.cb_juego['values']     = sorted(juegos)
        self.cb_motor['values']     = sorted(motores)
        self.cb_autor['values']     = sorted(autores)
        self.cb_trad['values']      = sorted(trads)

    # ---------- Eventos de campos ----------
    def _on_text_modified(self, event):
        w = event.widget
        if w.edit_modified():
            w.edit_modified(False)
            self._marcar_dirty()

    def _on_name_cambio(self, *args):
        if self._cargando or self._suprimir:
            return
        nuevo_der = derivar_folder(self.var_name.get().strip())
        actual = self.var_folder.get().strip()
        if not actual or actual == self._ultimo_folder_derivado:
            self.var_folder.set(nuevo_der)
            self._ultimo_folder_derivado = nuevo_der

    def _marcar_dirty(self, *args):
        if self._cargando or self._suprimir:
            return
        self.dirty = True
        self._actualizar_titulo()

    # ---------- Carga / commit ----------
    def _cargar(self):
        try:
            raw = cargar_catalogo(self.config_file)
        except Exception as e:
            log_error(f"No se pudo leer {self.config_file}: {e}", exc=sys.exc_info())
            messagebox.showerror("Error al cargar",
                                 f"No se pudo leer {self.config_file}:\n\n{e}\n\n"
                                 f"Log: {LOG_FILE_MANAGER}")
            raw = []

        self.mods = normalizar_todos(raw)

        # AUTO-FIX de IDs que sean URLs
        total_fixes = 0
        for m in self.mods:
            n = auto_fix_partes(m)
            if n:
                log_info(f"Auto-fix al cargar: '{m.get('name','?')}' — {n} ID(s) extraídos")
                total_fixes += n

        self.idx_actual = None
        self._actualizar_lista()
        self._limpiar_editor()
        self._refrescar_sugerencias()

        if total_fixes:
            self.dirty = True
            self._set_status(
                f"Cargados {len(self.mods)} mods. "
                f"{total_fixes} ID(s) extraídos de URLs — guarda para aplicar.")
            messagebox.showinfo(
                "IDs normalizados",
                f"Se detectaron {total_fixes} campo(s) 'id' que contenían URLs "
                f"y se extrajo el ID.\n\n"
                f"Se guardarán al pulsar Guardar.")
        else:
            self.dirty = False
            self._set_status(f"Cargados {len(self.mods)} mods desde "
                             f"{os.path.basename(self.config_file)}")

        self._actualizar_titulo()

    def _limpiar_editor(self):
        self._cargando = True
        try:
            for v in (self.var_id, self.var_name, self.var_version, self.var_autor,
                      self.var_trad, self.var_categoria, self.var_juego,
                      self.var_motor, self.var_folder):
                v.set('')
            for w in (self.txt_sinopsis, self.txt_desc):
                w.delete('1.0', tk.END)
                w.edit_modified(False)
            self._refrescar_tree(self.tree_parts, [])
            self._refrescar_tree(self.tree_complements, [])
            self.txt_preview.config(state='normal')
            self.txt_preview.delete('1.0', tk.END)
            self.txt_preview.config(state='disabled')
        finally:
            self._cargando = False

    def _comprometer_editor(self):
        if self._cargando or self.idx_actual is None or self.idx_actual >= len(self.mods):
            return
        m = self.mods[self.idx_actual]
        m['id']            = self.var_id.get().strip() or id_unico(
                                self.mods, derivar_id(self.var_name.get()),
                                excluir_idx=self.idx_actual)
        m['name']          = self.var_name.get().strip()
        m['version']       = self.var_version.get().strip() or '1.0'
        m['autor']         = self.var_autor.get().strip()
        m['trad']          = self.var_trad.get().strip()
        m['categoria']     = self.var_categoria.get().strip() or 'Sin clasificar'
        m['juego_base']    = self.var_juego.get().strip() or 'Desconocido'
        m['motor']         = self.var_motor.get().strip() or 'Desconocido'
        folder = self.var_folder.get().strip()
        if not folder:
            folder = derivar_folder(m['name'])
        m['mod_folder']    = sanitizar_nombre_archivo(folder)[:50].rstrip('. ') or 'mod'
        m['sinopsis']      = self.txt_sinopsis.get('1.0', 'end-1c').strip()
        m['descripcion_larga'] = self.txt_desc.get('1.0', 'end-1c').strip()

        # Auto-fix de las partes del mod actual (red de seguridad)
        n = auto_fix_partes(m)
        if n:
            log_info(f"Auto-fix al comprometer editor: '{m.get('name','?')}' — {n} ID(s)")
            self._refrescar_tree(self.tree_parts,       m.get('parts', []))
            self._refrescar_tree(self.tree_complements, m.get('complements', []))
            self._actualizar_preview()
            self.dirty = True
            self._actualizar_titulo()

    def _cargar_en_editor(self, mod):
        self._cargando = True
        try:
            self.var_id.set(mod.get('id', ''))
            self.var_name.set(mod.get('name', ''))
            self.var_version.set(mod.get('version', '1.0'))
            self.var_autor.set(mod.get('autor', ''))
            self.var_trad.set(mod.get('trad', ''))
            self.var_categoria.set(mod.get('categoria', ''))
            self.var_juego.set(mod.get('juego_base', ''))
            self.var_motor.set(mod.get('motor', ''))
            self.var_folder.set(mod.get('mod_folder', ''))
            self._ultimo_folder_derivado = derivar_folder(mod.get('name', ''))

            self.txt_sinopsis.delete('1.0', tk.END)
            self.txt_sinopsis.insert('1.0', mod.get('sinopsis', ''))
            self.txt_sinopsis.edit_modified(False)
            self.txt_desc.delete('1.0', tk.END)
            self.txt_desc.insert('1.0', mod.get('descripcion_larga', ''))
            self.txt_desc.edit_modified(False)

            self._refrescar_tree(self.tree_parts,       mod.get('parts', []))
            self._refrescar_tree(self.tree_complements, mod.get('complements', []))

            self._actualizar_preview()
        finally:
            self._cargando = False
        self._refrescar_sugerencias()

    def _refrescar_tree(self, tree, items, seleccionar=None):
        tree.delete(*tree.get_children())
        for p in items:
            tipo = p.get('type', '')
            loc = p.get('url', '') if tipo == 'direct' else p.get('id', '')
            tree.insert('', tk.END, values=(p.get('file_name', ''), tipo, loc))
        if seleccionar is not None:
            children = tree.get_children()
            if 0 <= seleccionar < len(children):
                tree.selection_set(children[seleccionar])

    def _actualizar_preview(self):
        if self.idx_actual is None or self.idx_actual >= len(self.mods):
            texto = ""
        else:
            texto = json.dumps(self.mods[self.idx_actual], indent=4, ensure_ascii=False)
        self.txt_preview.config(state='normal')
        self.txt_preview.delete('1.0', tk.END)
        self.txt_preview.insert('1.0', texto)
        self.txt_preview.config(state='disabled')

    # ---------- Lista ----------
    def _actualizar_lista(self):
        filtro = self.var_busqueda.get().strip().lower()
        idx_sel = self.idx_actual

        self._suprimir = True
        try:
            self.lista.delete(0, tk.END)
            self.mods_filtrados = []
            for i, m in enumerate(self.mods):
                if filtro:
                    blob = ' '.join(str(m.get(k, '')) for k in
                                    ('name', 'autor', 'categoria',
                                     'juego_base', 'motor', 'trad')).lower()
                    if filtro not in blob:
                        continue
                self.mods_filtrados.append((i, m))
                nombre = m.get('name') or '(sin nombre)'
                ver = m.get('version') or '?'
                self.lista.insert(tk.END, f"{i + 1:>3}.  {nombre}  (v{ver})")

            if idx_sel is not None:
                for pos, (i, _) in enumerate(self.mods_filtrados):
                    if i == idx_sel:
                        self.lista.selection_set(pos)
                        self.lista.see(pos)
                        break
        finally:
            self._suprimir = False

        txt = f"{len(self.mods)} mods"
        if filtro:
            txt += f" — {len(self.mods_filtrados)} visibles"
        self.lbl_contador.config(text=txt)
        self._actualizar_botones_mover()

    # ---------- Reordenamiento de mods ----------
    def _actualizar_botones_mover(self):
        filtro_activo = bool(self.var_busqueda.get().strip())
        idx = self.idx_actual
        hay_sel = idx is not None and 0 <= idx < len(self.mods)
        puede = hay_sel and not filtro_activo

        self.btn_mod_inicio.config(state='normal' if puede and idx > 0 else 'disabled')
        self.btn_mod_arriba.config(state='normal' if puede and idx > 0 else 'disabled')
        self.btn_mod_abajo.config(state='normal' if puede and idx < len(self.mods) - 1 else 'disabled')
        self.btn_mod_final.config(state='normal' if puede and idx < len(self.mods) - 1 else 'disabled')

        if hay_sel:
            txt = f"Posición: {idx + 1} de {len(self.mods)}"
            if filtro_activo:
                txt += "  (filtro activo — limpia la búsqueda para reordenar)"
            self.lbl_posicion.config(text=txt)
        else:
            self.lbl_posicion.config(text="")

    def _puede_reordenar(self) -> bool:
        if self.var_busqueda.get().strip():
            messagebox.showinfo("Filtro activo",
                "Limpia la búsqueda para reordenar mods.\n\n"
                "Así sabes exactamente en qué posición del catálogo queda cada mod.")
            return False
        return self.idx_actual is not None and 0 <= self.idx_actual < len(self.mods)

    def _mover_mod(self, delta):
        if not self._puede_reordenar():
            return
        idx = self.idx_actual
        nuevo = idx + delta
        if not (0 <= nuevo < len(self.mods)):
            return
        self._comprometer_editor()
        self.mods[idx], self.mods[nuevo] = self.mods[nuevo], self.mods[idx]
        self.idx_actual = nuevo
        self.dirty = True
        self._actualizar_lista()
        self._actualizar_titulo()
        nombre = self.mods[nuevo].get('name', '?')
        self._set_status(f"'{nombre}' movido a la posición {nuevo + 1} de {len(self.mods)}.")

    def _mover_mod_a(self, pos):
        if not self._puede_reordenar():
            return
        idx = self.idx_actual
        if pos < 0 or pos >= len(self.mods) or pos == idx:
            return
        self._comprometer_editor()
        mod = self.mods.pop(idx)
        self.mods.insert(pos, mod)
        self.idx_actual = pos
        self.dirty = True
        self._actualizar_lista()
        self._actualizar_titulo()
        self._set_status(f"'{mod.get('name', '?')}' movido a la posición {pos + 1} de {len(self.mods)}.")

    def _on_key_mover_arriba(self, _e):
        self._mover_mod(-1); return 'break'
    def _on_key_mover_abajo(self, _e):
        self._mover_mod(1); return 'break'
    def _on_key_mover_inicio(self, _e):
        self._mover_mod_a(0); return 'break'
    def _on_key_mover_final(self, _e):
        self._mover_mod_a(len(self.mods) - 1); return 'break'

    def _on_seleccionar(self, _event=None):
        if self._suprimir:
            return
        sel = self.lista.curselection()
        if not sel:
            self._actualizar_botones_mover()
            return
        pos = sel[0]
        if pos >= len(self.mods_filtrados):
            self._actualizar_botones_mover()
            return
        idx = self.mods_filtrados[pos][0]
        if idx == self.idx_actual:
            self._actualizar_botones_mover()
            return
        self._comprometer_editor()
        self.idx_actual = idx
        self._cargar_en_editor(self.mods[idx])
        self._actualizar_botones_mover()

    def _seleccionar_en_lista(self, idx):
        for pos, (i, _) in enumerate(self.mods_filtrados):
            if i == idx:
                self._suprimir = True
                try:
                    self.lista.selection_clear(0, tk.END)
                    self.lista.selection_set(pos)
                    self.lista.see(pos)
                finally:
                    self._suprimir = False
                return

    # ---------- Acciones principales ----------
    def _nuevo_mod(self):
        self._comprometer_editor()
        nombre = "Nuevo Mod"
        nuevo = {
            'id': id_unico(self.mods, derivar_id(nombre)),
            'name': nombre,
            'version': '1.0',
            'autor': '',
            'trad': '',
            'sinopsis': '',
            'descripcion_larga': '',
            'categoria': 'Campaña',
            'juego_base': 'Call of Pripyat',
            'motor': 'Vanilla',
            'mod_folder': derivar_folder(nombre),
            'parts': [],
            'complements': [],
        }
        self.mods.append(nuevo)
        self.idx_actual = len(self.mods) - 1
        self.dirty = True
        self._actualizar_lista()
        self._seleccionar_en_lista(self.idx_actual)
        self._cargar_en_editor(nuevo)
        self._actualizar_titulo()
        self.var_name.focus_set()
        self.var_name.select_range(0, tk.END)
        self._set_status("Nuevo mod creado (sin guardar).")

    def _eliminar_mod(self):
        if self.idx_actual is None:
            messagebox.showinfo("Sin selección", "Selecciona un mod para eliminar.")
            return
        m = self.mods[self.idx_actual]
        if not messagebox.askyesno("Eliminar",
                f"¿Eliminar el mod '{m.get('name', '?')}'?\n\n"
                "El cambio no se guarda hasta pulsar Guardar."):
            return
        del self.mods[self.idx_actual]
        self.idx_actual = None
        self.dirty = True
        self._actualizar_lista()
        self._limpiar_editor()
        self._refrescar_sugerencias()
        self._actualizar_titulo()
        self._set_status("Mod eliminado (sin guardar).")

    def _duplicar_mod(self):
        if self.idx_actual is None:
            return
        self._comprometer_editor()
        orig = self.mods[self.idx_actual]
        copia = json.loads(json.dumps(orig))
        copia['name'] = (orig.get('name') or 'mod') + ' (copia)'
        copia['id']   = id_unico(self.mods, derivar_id(copia['name']))
        copia['mod_folder'] = derivar_folder(copia['name'])
        self.mods.append(copia)
        self.idx_actual = len(self.mods) - 1
        self.dirty = True
        self._actualizar_lista()
        self._seleccionar_en_lista(self.idx_actual)
        self._cargar_en_editor(copia)
        self._actualizar_titulo()

    def _guardar(self):
        try:
            self._comprometer_editor()
        except Exception as e:
            log_error(f"Error en _comprometer_editor: {e}", exc=sys.exc_info())
            messagebox.showerror("Error interno",
                f"No se pudo preparar el mod para guardar:\n\n{e}\n\n"
                f"Log: {LOG_FILE_MANAGER}")
            return

        # Segunda pasada de auto-fix (por si algún mod no pasó por el editor)
        total_fix = 0
        for m in self.mods:
            n = auto_fix_partes(m)
            if n:
                log_info(f"Auto-fix al guardar: '{m.get('name','?')}' — {n} ID(s)")
                total_fix += n

        try:
            backup = guardar_atomico(self.mods, self.config_file, validar=True)
            log_info(f"Guardado OK: {len(self.mods)} mods en {self.config_file}"
                     + (f" (fix={total_fix})" if total_fix else ""))
        except ValueError as e:
            log_error(f"Validación fallida al guardar: {e}")
            messagebox.showerror("Validación fallida",
                f"{e}\n\nLog: {LOG_FILE_MANAGER}")
            return
        except PermissionError as e:
            log_error(f"Permiso denegado al guardar: {e}", exc=sys.exc_info())
            messagebox.showerror("Archivo bloqueado",
                f"No se pudo reemplazar {os.path.basename(self.config_file)}:\n\n{e}\n\n"
                "¿Está abierto en otro programa (p. ej. el launcher)?\n\n"
                f"Log: {LOG_FILE_MANAGER}")
            return
        except Exception as e:
            log_error(f"Error inesperado al guardar: {e}", exc=sys.exc_info())
            messagebox.showerror("Error al guardar",
                f"No se pudo guardar:\n\n{e}\n\n"
                f"Detalle completo en:\n{LOG_FILE_MANAGER}")
            return

        self.dirty = False
        self._refrescar_sugerencias()
        self._actualizar_titulo()
        msg = f"Guardado en {os.path.basename(self.config_file)}"
        if total_fix:
            msg += f"\n\nSe extrajeron {total_fix} ID(s) de URLs automáticamente."
        if backup:
            try:
                rel = os.path.relpath(backup, SCRIPT_DIR)
            except Exception:
                rel = backup
            msg += f"\nBackup: {rel}"
        self._set_status(msg)
        messagebox.showinfo("Guardado", msg)

    def _reformatear(self):
        self._comprometer_editor()
        if self.dirty:
            if not messagebox.askyesno("Reformatear",
                    "Hay cambios sin guardar.\n¿Reformatear el archivo en disco "
                    "(descartando esos cambios)?"):
                return
            self._recargar(silencioso=True)
        # Auto-fix antes de guardar
        for m in self.mods:
            auto_fix_partes(m)
        try:
            backup = guardar_atomico(self.mods, self.config_file, validar=True)
            log_info(f"Reformateo OK: {len(self.mods)} mods")
        except Exception as e:
            log_error(f"Error al reformatear: {e}", exc=sys.exc_info())
            messagebox.showerror("Error al reformatear",
                f"{e}\n\nLog: {LOG_FILE_MANAGER}")
            return
        self.dirty = False
        self._refrescar_sugerencias()
        self._actualizar_titulo()
        messagebox.showinfo("Reformateado",
                            f"Archivo guardado con indent=4, UTF-8, LF.\n"
                            f"{len(self.mods)} mods."
                            + (f"\nBackup: {backup}" if backup else ""))

    def _recargar(self, silencioso=False):
        if self.dirty and not silencioso:
            if not messagebox.askyesno("Recargar",
                    "Hay cambios sin guardar. ¿Descartarlos y recargar desde disco?"):
                return
        self._cargar()

    def _abrir(self):
        if self.dirty:
            if not messagebox.askyesno("Abrir",
                    "Hay cambios sin guardar. ¿Descartarlos y abrir otro archivo?"):
                return
        ruta = filedialog.askopenfilename(
            title="Abrir catálogo de mods",
            initialdir=os.path.dirname(self.config_file),
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")])
        if not ruta:
            return
        self.config_file = ruta
        self._cargar()

    def _validar_ui(self):
        self._comprometer_editor()
        problemas = validar_catalogo(self.mods)
        if not problemas:
            messagebox.showinfo("Validación",
                                f"✓ Catálogo válido ({len(self.mods)} mods).")
            return
        log_error(f"Validación manual: {len(problemas)} problema(s)")
        cabecera = f"{len(problemas)} problema(s) encontrados:\n\n"
        cuerpo = "\n".join(f"• {p}" for p in problemas[:40])
        if len(problemas) > 40:
            cuerpo += f"\n… y {len(problemas) - 40} más."
        messagebox.showwarning("Validación",
                               cabecera + cuerpo + f"\n\nLog: {LOG_FILE_MANAGER}")

    def _al_cerrar(self):
        self._comprometer_editor()
        if self.dirty:
            r = messagebox.askyesnocancel(
                "Cambios sin guardar",
                "Hay cambios sin guardar. ¿Deseas guardarlos antes de cerrar?")
            if r is None:
                return
            if r:
                self._guardar()
                if self.dirty:
                    return
        self.root.destroy()

    def _abrir_log(self):
        if os.path.exists(LOG_FILE_MANAGER):
            try:
                os.startfile(LOG_FILE_MANAGER)
            except AttributeError:
                messagebox.showinfo("Log", f"Ruta:\n{LOG_FILE_MANAGER}")
        else:
            messagebox.showinfo("Sin log",
                                f"Aún no hay errores registrados.\n\n"
                                f"Ruta: {LOG_FILE_MANAGER}")

    def _acerca_de(self):
        messagebox.showinfo("Acerca de",
            f"Gestor de Mods v{VERSION_MANAGER} — KamteK Launcher\n\n"
            "• Reordenar mods (↑/↓/Inicio/Final)\n"
            "• Sugerencias de categoría / juego / motor / autor / trad\n"
            "• Auto-detección de tipo y extracción de ID desde URLs\n"
            "• Auto-fix de 'id' con URL al cargar / editar / guardar\n"
            "• Guardado ATÓMICO (temp → hash → os.replace)\n"
            "• Verificación tras escritura (MD5 + re-parseo)\n"
            "• Backup automático (.bak + backups/<fecha>.json)\n"
            "• Indentación canónica (indent=4, UTF-8, LF)\n"
            "• Log de errores con traceback completo\n\n"
            f"Log:\n{LOG_FILE_MANAGER}")

    # ---------- Partes / complementos ----------
    def _añadir_parte(self, key):
        if self.idx_actual is None:
            return
        self._comprometer_editor()
        dlg = PartDialog(self.root)
        self.root.wait_window(dlg)
        if dlg.resultado is None:
            return
        items = self.mods[self.idx_actual].setdefault(key, [])
        items.append(dlg.resultado)
        self.dirty = True
        self._refrescar_tree(self._tree_de(key), items, seleccionar=len(items) - 1)
        self._actualizar_preview()
        self._actualizar_titulo()

    def _editar_parte(self, key):
        if self.idx_actual is None:
            return
        tree = self._tree_de(key)
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Sin selección", "Selecciona un archivo para editar.")
            return
        pos = tree.index(sel[0])
        items = self.mods[self.idx_actual].get(key, [])
        if pos >= len(items):
            return
        dlg = PartDialog(self.root, items[pos])
        self.root.wait_window(dlg)
        if dlg.resultado is None:
            return
        items[pos] = dlg.resultado
        self.dirty = True
        self._refrescar_tree(tree, items, seleccionar=pos)
        self._actualizar_preview()
        self._actualizar_titulo()

    def _eliminar_parte(self, key):
        if self.idx_actual is None:
            return
        tree = self._tree_de(key)
        sel = tree.selection()
        if not sel:
            return
        pos = tree.index(sel[0])
        items = self.mods[self.idx_actual].get(key, [])
        if pos >= len(items):
            return
        if not messagebox.askyesno("Eliminar",
                f"¿Eliminar '{items[pos].get('file_name', '?')}'?"):
            return
        del items[pos]
        self.dirty = True
        nueva_sel = min(pos, len(items) - 1) if items else None
        self._refrescar_tree(tree, items, seleccionar=nueva_sel)
        self._actualizar_preview()
        self._actualizar_titulo()

    def _mover_parte(self, key, delta):
        if self.idx_actual is None:
            return
        tree = self._tree_de(key)
        sel = tree.selection()
        if not sel:
            return
        pos = tree.index(sel[0])
        items = self.mods[self.idx_actual].get(key, [])
        nuevo = pos + delta
        if not (0 <= nuevo < len(items)):
            return
        items[pos], items[nuevo] = items[nuevo], items[pos]
        self.dirty = True
        self._refrescar_tree(tree, items, seleccionar=nuevo)
        self._actualizar_preview()
        self._actualizar_titulo()


# ============================================================
# Modo CLI
# ============================================================
def _cli_pedir(campo, obligatorio=True, por_defecto=""):
    sufijo = "" if obligatorio else " (opcional, Enter para saltar)"
    if por_defecto:
        sufijo += f" [{por_defecto}]"
    while True:
        valor = input(f"{campo}{sufijo}: ").strip()
        if valor:
            return valor
        if por_defecto:
            return por_defecto
        if not obligatorio:
            return ""
        print("  ⚠ Este campo es obligatorio.")


def _cli_pedir_multilinea(campo, obligatorio=True):
    print(f"{campo} (escribe '.' en una línea vacía para terminar):")
    lineas = []
    while True:
        l = input()
        if l.strip() == ".":
            break
        lineas.append(l)
    texto = "\n".join(lineas).strip()
    if obligatorio and not texto:
        print("  ⚠ Este campo es obligatorio.")
        return _cli_pedir_multilinea(campo, obligatorio)
    return texto


def _cli_pedir_parte(numero):
    print(f"\n--- Archivo #{numero} ---")
    file_name = _cli_pedir("  file_name")
    print("  Tipos disponibles: gdrive, moddb, direct")
    tipo = _cli_pedir("  type", por_defecto="gdrive").lower()
    while tipo not in VALID_TYPES:
        print("  ⚠ Tipo inválido.")
        tipo = _cli_pedir("  type", por_defecto="gdrive").lower()
    if tipo == "direct":
        loc = _cli_pedir("  url")
        return {"file_name": file_name, "type": tipo, "url": loc}
    loc = _cli_pedir("  id (puedes pegar la URL completa)")
    original = loc
    loc = extraer_id_segun_tipo(loc, tipo)
    if loc != original:
        print(f"  ✓ ID extraído: {loc}")
    return {"file_name": file_name, "type": tipo, "id": loc}


def _cli_pedir_lista(categoria):
    print(f"\n=== {categoria.upper()} ===")
    archivos = []
    while True:
        archivos.append(_cli_pedir_parte(len(archivos) + 1))
        if input("\n¿Añadir otro archivo? (s/n): ").strip().lower() != "s":
            break
    return archivos


def main_cli(config_path):
    print("=" * 60)
    print(f"  AÑADIR MOD AL CATÁLOGO — KamteK Launcher (v{VERSION_MANAGER})")
    print("=" * 60)

    try:
        mods = normalizar_todos(cargar_catalogo(config_path))
        for m in mods:
            n = auto_fix_partes(m)
            if n:
                print(f"  ℹ Auto-fix: '{m.get('name','?')}' — {n} ID(s) extraídos")
    except Exception as e:
        print(f"  ⚠ No se pudo cargar {config_path}: {e}")
        sys.exit(1)

    ids = {m.get("id") for m in mods}
    print(f"  Mods actuales en el catálogo: {len(mods)}\n")

    cats = sorted({m.get('categoria') for m in mods if m.get('categoria')})
    juegos = sorted({m.get('juego_base') for m in mods if m.get('juego_base')})
    motores = sorted({m.get('motor') for m in mods if m.get('motor')})

    mod = {}
    while True:
        mod["id"] = _cli_pedir("ID interno (sin espacios)")
        if mod["id"] in ids:
            print(f"  ⚠ Ya existe un mod con id '{mod['id']}'.")
            continue
        break
    mod["name"]  = _cli_pedir("Nombre completo")
    mod["version"] = _cli_pedir("Versión", por_defecto="1.0")
    mod["autor"] = _cli_pedir("Autor original")
    mod["trad"]  = _cli_pedir("Traductor/es")
    mod["sinopsis"] = _cli_pedir_multilinea("Sinopsis breve")
    mod["descripcion_larga"] = _cli_pedir_multilinea("Descripción larga")

    if cats:
        print(f"  Categorías existentes: {', '.join(cats)}")
    mod["categoria"]  = _cli_pedir("Categoría", por_defecto="Campaña")

    if juegos:
        print(f"  Juegos existentes: {', '.join(juegos)}")
    mod["juego_base"] = _cli_pedir("Juego base", por_defecto="Call of Pripyat")

    if motores:
        print(f"  Motores existentes: {', '.join(motores)}")
    mod["motor"]      = _cli_pedir("Motor", por_defecto="Vanilla")

    mod["mod_folder"] = _cli_pedir("Carpeta", por_defecto=derivar_folder(mod["name"]))

    mod["parts"] = _cli_pedir_lista("partes")
    if input("\n¿Añadir complementos? (s/n): ").strip().lower() == "s":
        mod["complements"] = _cli_pedir_lista("complementos")
    else:
        mod["complements"] = []

    print("\n" + "=" * 60)
    print("  VISTA PREVIA")
    print("=" * 60)
    print(json.dumps(mod, indent=4, ensure_ascii=False))

    print("\n¿Qué hacer?")
    print("  1) Insertar en mods_config.json")
    print("  2) Solo mostrar el bloque")
    print("  3) Cancelar")
    op = input("Opción [1/2/3]: ").strip()

    if op == "1":
        mods.append(mod)
        try:
            backup = guardar_atomico(mods, config_path, validar=True)
        except ValueError as e:
            log_error(f"Validación fallida en CLI: {e}")
            print(f"\n⚠ Validación: {e}")
            if input("¿Guardar de todos modos? (s/n): ").strip().lower() != "s":
                print("Cancelado.")
                return
            backup = guardar_atomico(mods, config_path, validar=False)
        except Exception as e:
            log_error(f"Error al guardar en CLI: {e}", exc=sys.exc_info())
            print(f"\n❌ Error al guardar: {e}\n   Log: {LOG_FILE_MANAGER}")
            return
        print(f"\n✅ Mod '{mod['name']}' añadido. Total: {len(mods)}")
        if backup:
            print(f"   Backup: {backup}")
    elif op == "2":
        print("\n📋 Copia el bloque manualmente.")
    else:
        print("\n❌ Cancelado.")


# ============================================================
# Modo CHECK
# ============================================================
def main_check(config_path):
    try:
        raw = cargar_catalogo(config_path)
    except Exception as e:
        log_error(f"CHECK: no se pudo leer {config_path}: {e}", exc=sys.exc_info())
        print(f"ERROR: no se pudo leer {config_path}: {e}")
        sys.exit(1)

    mods = normalizar_todos(raw)
    fixes = 0
    for m in mods:
        fixes += auto_fix_partes(m)

    problemas = validar_catalogo(mods)
    if problemas:
        for p in problemas:
            log_error(f"CHECK: {p}")
        print(f"✗ {len(problemas)} problema(s):")
        for p in problemas:
            print(f"  • {p}")
        print(f"\nLog: {LOG_FILE_MANAGER}")
        sys.exit(1)

    try:
        backup = guardar_atomico(mods, config_path, validar=True)
    except Exception as e:
        log_error(f"CHECK: error al reformatear: {e}", exc=sys.exc_info())
        print(f"ERROR al reformatear: {e}")
        sys.exit(1)

    print(f"✓ {len(mods)} mods válidos, reformateados."
          + (f" ({fixes} ID(s) extraídos de URL)" if fixes else ""))
    if backup:
        print(f"  Backup: {backup}")


# ============================================================
# Entrada
# ============================================================
def _excepthook(exc_type, exc_value, exc_tb):
    log_error(f"Excepción no controlada: {exc_type.__name__}: {exc_value}",
              exc=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    sys.excepthook = _excepthook

    ap = argparse.ArgumentParser(
        description=f"Gestor de mods_config.json — KamteK Launcher (v{VERSION_MANAGER})")
    ap.add_argument('--cli',     action='store_true', help="Asistente por consola")
    ap.add_argument('--check',   action='store_true', help="Validar + reformatear (sin GUI)")
    ap.add_argument('--file',    default=CONFIG_FILE_DEFAULT,
                    help="Ruta a mods_config.json")
    ap.add_argument('--version', action='version',
                    version=f"mod_manager {VERSION_MANAGER}")
    args = ap.parse_args()

    if args.check:
        main_check(args.file)
        return
    if args.cli:
        main_cli(args.file)
        return

    root = tk.Tk()
    try:
        ModManagerApp(root, args.file)
        root.mainloop()
    except Exception as e:
        log_error(f"Error fatal en mainloop: {e}", exc=sys.exc_info())
        try:
            messagebox.showerror("Error fatal",
                f"{e}\n\nRevisa:\n{LOG_FILE_MANAGER}")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(0)
