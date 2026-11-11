"""Which traces a signal judges: a signal version's filters, checked against one trace."""

# (field, op) pairs this code understands. Anything else is rejected, never silently ignored.
SUPPORTED = {("status", "eq")}


class FilterError(ValueError):
    """A filter this code doesn't understand."""


def matches(filters, trace):
    """True if the trace passes every filter. An empty list matches every trace.

    filters: a signal version's filters, e.g. [{"field": "status", "op": "eq", "value": "error"}]
    trace:   the trace's fields, e.g. {"status": "error"}
    """
    for f in filters:
        field, op, value = f.get("field"), f.get("op"), f.get("value")
        if (field, op) not in SUPPORTED:
            raise FilterError(f"unsupported filter: {f}")
        if trace[field] != value:
            return False
    return True