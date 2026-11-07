"""Select an option through the visible, keyboard-accessible Radix menu."""
import json


def choose_select(control, value):
    control.click()
    control.page.locator('[role="option"][data-select-value=' + json.dumps(str(value), ensure_ascii=False) + ']').click()
