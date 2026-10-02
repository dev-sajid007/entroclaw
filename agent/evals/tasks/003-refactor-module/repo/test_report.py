from report import expense_report, sales_report

ROWS = [(" widgets ", 12.5), ("gadgets", 3)]
EXPECTED = "Widgets                  12.50\nGadgets                   3.00"


def test_sales():
    assert sales_report(ROWS) == EXPECTED


def test_expense():
    assert expense_report(ROWS) == EXPECTED
