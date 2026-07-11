# Task: csv_field_extract

Read `employees.csv` in this directory and write a `summary.json` file
into your output directory with this exact shape:

```json
{
  "total_employees": <int>,
  "by_department": {"<department>": <count>, ...},
  "highest_paid_name": "<name of the employee with the highest salary>"
}
```

- `total_employees`: total number of data rows in the CSV.
- `by_department`: count of employees per `department` value.
- `highest_paid_name`: the `name` of the row with the maximum `salary`
  (salaries are unique in this dataset, so there is exactly one answer).

This is a state-based agentic task: your submission is judged purely by
inspecting the final `summary.json` you produce, mechanically compared
against the correct answer (numeric/dict fields exactly, the name field
via a case/whitespace-insensitive quasi-exact match).
