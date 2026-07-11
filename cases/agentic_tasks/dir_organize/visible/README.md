# Task: dir_organize

Reorganize the files in `messy/` into your output directory, grouped into
subdirectories by (lowercase) file extension. For example, `report.txt`
must end up at `<output>/txt/report.txt` and `photo.jpg` at
`<output>/jpg/photo.jpg` -- byte-for-byte identical to the source file,
same filename, extension name without the leading dot.

Requirements:

- One subdirectory per extension present in `messy/`, named by the
  extension (lowercase, no dot) -- e.g. `txt`, `jpg`, `png`, `csv`, `py`.
- Every file from `messy/` must appear under its extension's
  subdirectory, with the same filename and unchanged (byte-identical)
  content.
- No extra top-level subdirectories and no extra files within any
  extension subdirectory beyond what `messy/` contains.

This is a state-based agentic task: your submission is judged purely by
inspecting the final directory tree you produce, mechanically compared
against the correct reorganization of `messy/`.
