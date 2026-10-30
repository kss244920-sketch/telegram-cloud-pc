import base64
import logging
import requests
from typing import Any, List, Optional, Union

logger = logging.getLogger("TursoClient")

class TursoRow(dict):
    """
    Drop-in replacement for sqlite3.Row.
    Supports dict-style key access ('balance'), index access (row[0]), and dict(row).
    """
    def __init__(self, cols: List[str], values: List[Any]):
        super().__init__(zip(cols, values))
        self._cols = cols
        self._values = values

    def __getitem__(self, key: Union[str, int]) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def keys(self):
        return self._cols

    def values(self):
        return self._values

    def items(self):
        return zip(self._cols, self._values)


class TursoCursor:
    def __init__(self, conn: "TursoConnection"):
        self.conn = conn
        self._rows: List[TursoRow] = []
        self._cols: List[str] = []
        self._idx: int = 0
        self.lastrowid: Optional[int] = None

    def execute(self, sql: str, params: Any = None):
        res = self.conn._execute_http(sql, params)
        self._cols = [c.get("name", "") for c in res.get("cols", [])]
        self._rows = []
        for raw in res.get("rows", []):
            row_vals = []
            for cell in raw:
                ctype = cell.get("type")
                if ctype == "null":
                    row_vals.append(None)
                elif ctype == "integer":
                    row_vals.append(int(cell["value"]))
                elif ctype == "float":
                    row_vals.append(float(cell["value"]))
                elif ctype == "blob":
                    row_vals.append(base64.b64decode(cell.get("base64", "")))
                else:
                    row_vals.append(cell.get("value"))
            self._rows.append(TursoRow(self._cols, row_vals))
        self._idx = 0

        lid = res.get("last_insert_rowid")
        if lid is not None:
            try:
                self.lastrowid = int(lid)
            except Exception:
                self.lastrowid = None
        return self

    def executemany(self, sql: str, seq_of_params: List[Any]):
        for params in seq_of_params:
            self.execute(sql, params)
        return self

    def fetchone(self) -> Optional[TursoRow]:
        if self._idx < len(self._rows):
            r = self._rows[self._idx]
            self._idx += 1
            return r
        return None

    def fetchall(self) -> List[TursoRow]:
        res = self._rows[self._idx:]
        self._idx = len(self._rows)
        return res


class TursoConnection:
    _shared_session: Optional[requests.Session] = None

    def __init__(self, url: str, token: str):
        if url.startswith("libsql://"):
            url = url.replace("libsql://", "https://")
        elif not url.startswith("https://") and not url.startswith("http://"):
            url = f"https://{url}"
        self.endpoint = f"{url.rstrip('/')}/v2/pipeline"
        self.token = token
        self.row_factory = None
        self._init_session()

    def _init_session(self):
        if TursoConnection._shared_session is None:
            s = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=15, pool_maxsize=30, max_retries=2)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            s.headers.update({
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            })
            TursoConnection._shared_session = s

    @property
    def session(self) -> requests.Session:
        return TursoConnection._shared_session

    def _convert_param(self, p: Any) -> dict:
        if p is None:
            return {"type": "null"}
        elif isinstance(p, bool):
            return {"type": "integer", "value": "1" if p else "0"}
        elif isinstance(p, int):
            return {"type": "integer", "value": str(p)}
        elif isinstance(p, float):
            return {"type": "float", "value": p}
        elif isinstance(p, bytes):
            return {"type": "blob", "base64": base64.b64encode(p).decode("ascii")}
        else:
            return {"type": "text", "value": str(p)}

    def _execute_http(self, sql: str, params: Any = None) -> dict:
        stmt = {"sql": sql}
        if params is not None:
            if not isinstance(params, (list, tuple)):
                params = [params]
            stmt["args"] = [self._convert_param(p) for p in params]
        payload = {"requests": [{"type": "execute", "stmt": stmt}]}
        resp = self.session.post(self.endpoint, json=payload, timeout=15)
        if resp.status_code != 200:
            raise Exception(f"Turso HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        first_res = data["results"][0]
        if first_res["type"] == "error":
            raise Exception(f"Turso SQL Error: {first_res['error']['message']}")
        return first_res["response"]["result"]

    def cursor(self) -> TursoCursor:
        return TursoCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
