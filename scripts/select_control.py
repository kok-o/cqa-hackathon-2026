"""Select an option through the visible, keyboard-accessible Radix menu."""
import json
import re

from playwright.sync_api import expect


def select_control(scope, label):
    """Radix's accessible name includes the wrapping label and selected text."""
    return scope.get_by_role('combobox', name=re.compile('^' + re.escape(label)))


def _option(control, value):
    return control.page.locator(
        '[role="option"][data-select-value=' + json.dumps(str(value), ensure_ascii=False) + ']'
    )


def choose_select(control, value):
    control.click()
    _option(control, value).click()
    expect(control).to_have_attribute('aria-expanded', 'false')


def expect_select_value(control, value):
    """Assert the actual selected value, without native-select-only APIs."""
    control.click()
    expect(_option(control, value)).to_have_attribute('data-state', 'checked')
    control.page.keyboard.press('Escape')
    expect(control).to_have_attribute('aria-expanded', 'false')


def set_filters_open(page, opened):
    """The filter overlay must be dismissed before clicking a queue row."""
    toggle = page.get_by_role('button', name=re.compile('^Фильтры'))
    expect(toggle).to_be_visible()
    if (toggle.get_attribute('aria-expanded') == 'true') != opened:
        toggle.click()
    expect(toggle).to_have_attribute('aria-expanded', str(opened).lower())
    if opened:
        expect(page.locator('#queue-filters')).to_be_visible()
    else:
        expect(page.locator('#queue-filters')).to_have_count(0)
