"""列表搜索用的可见标签匹配。"""

from sqlalchemy import false


def label_matches(column, keyword: str, labels: dict):
    """只匹配页面实际显示的标签文字，不把内部枚举值暴露为额外命中项。"""
    values = [value for value, label in labels.items() if keyword.casefold() in label.casefold()]
    return column.in_(values) if values else false()
