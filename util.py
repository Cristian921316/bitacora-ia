"""Utilidades pequeñas compartidas."""


def solo_digitos(s: str) -> str:
    return "".join(c for c in str(s or "") if c.isdigit())


def normalizar_telefono(s: str) -> str:
    """Deja solo dígitos. Un celular local de Ecuador pasa al formato internacional:
    0988391178 -> 593988391178, y 988391178 (Excel suele quitar el cero inicial) -> 593988391178."""
    d = solo_digitos(s)
    if len(d) == 10 and d.startswith("0"):
        return "593" + d[1:]
    if len(d) == 9 and d.startswith("9"):
        return "593" + d
    return d
