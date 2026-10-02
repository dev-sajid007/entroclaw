def sales_report(rows):
    lines = []
    for name, amount in rows:
        lines.append(f"{name.strip().title():<20}{amount:>10.2f}")
    return "\n".join(lines)


def expense_report(rows):
    lines = []
    for name, amount in rows:
        lines.append(f"{name.strip().title():<20}{amount:>10.2f}")
    return "\n".join(lines)
