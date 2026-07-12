# Agentic task: ini_to_json

Read `visible/config.ini` and write `<submission_dir>/config.json` — the INI
converted to a nested JSON object, one object per section.

Parsing rules (apply them exactly — the output is judged for an exact match):

- A line `[name]` opens a section named `name`.
- A line `key = value` adds `key` to the current section.
- **Trim** surrounding whitespace from both the key and the value. Whitespace
  *inside* a value is preserved (`format = json lines` → `"json lines"`).
- Lines whose first non-space character is `;` or `#` are comments — ignore
  them. Ignore blank lines.
- **Every value is a string** — do not coerce `8080` to a number.
- There are no duplicate sections or keys, and every `key = value` line
  appears inside a section.

Example: the `[server]` section above becomes

```json
"server": {"host": "0.0.0.0", "port": "8080", "workers": "4"}
```

so the full `config.json` is a top-level object `{"server": {...}, "database":
{...}, "logging": {...}}`.
