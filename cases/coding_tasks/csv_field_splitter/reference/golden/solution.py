"""Golden solution for csv_field_splitter (case admission evidence; not
scored). Single-pass state-machine parser handling RFC4180-style quoting
and doubled-quote (\"\") escaping within a quoted field."""


def split_csv_line(line: str) -> list[str]:
    """Split a single CSV line into fields, honoring RFC4180-style quoting."""
    fields: list[str] = []
    field_chars: list[str] = []
    in_quotes = False
    i = 0
    n = len(line)
    while i < n:
        char = line[i]
        if in_quotes:
            if char == '"':
                if i + 1 < n and line[i + 1] == '"':
                    field_chars.append('"')
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            field_chars.append(char)
            i += 1
            continue
        if char == '"':
            in_quotes = True
            i += 1
            continue
        if char == ",":
            fields.append("".join(field_chars))
            field_chars = []
            i += 1
            continue
        field_chars.append(char)
        i += 1
    fields.append("".join(field_chars))
    return fields
