def last_n_items(items, n):
    """Return the last n items of the list."""
    return items[len(items) - n - 1:]


def total_quantity(stock):
    return sum(qty for _, qty in stock)
