import { Children, forwardRef, isValidElement, useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode, SelectHTMLAttributes } from 'react';
import * as Menu from '@radix-ui/react-select';
import { Check, ChevronDown } from 'lucide-react';
import './Select.css';

type Props = SelectHTMLAttributes<HTMLSelectElement> & { selectedLabel?: ReactNode };
type Option = { value: string; label: ReactNode; disabled?: boolean };
const EMPTY = '__cqa_empty_option__';
const inertOwners = new WeakMap<HTMLElement, { count: number; previous: boolean }>();

/** Radix hides the background from AT; inert also prevents programmatic focus. */
function isolateMenu(content: HTMLElement) {
  const background: HTMLElement[] = [];
  let branch: HTMLElement | null = content;
  while (branch?.parentElement) {
    const parent: HTMLElement = branch.parentElement;
    for (const sibling of Array.from(parent.children)) {
      if (!(sibling instanceof HTMLElement) || sibling === branch ||
        sibling.matches('script, style, link, [data-radix-focus-guard]')) continue;
      const owner = inertOwners.get(sibling) ?? { count: 0, previous: sibling.inert };
      owner.count += 1;
      inertOwners.set(sibling, owner);
      sibling.inert = true;
      background.push(sibling);
    }
    if (parent === document.body) break;
    branch = parent;
  }
  return () => {
    for (const element of background) {
      const owner = inertOwners.get(element);
      if (!owner || --owner.count > 0) continue;
      element.inert = owner.previous;
      inertOwners.delete(element);
    }
  };
}

/** Shared keyboard-accessible select; menu stays inside a modal when needed. */
const Select = forwardRef<HTMLButtonElement, Props>(function Select({ children, value, defaultValue, onChange, disabled, name, id, className, title, required, selectedLabel, 'aria-label': ariaLabel, 'aria-labelledby': labelledBy, 'aria-describedby': describedBy }, forwardedRef) {
  const trigger = useRef<HTMLButtonElement | null>(null);
  const [container, setContainer] = useState<HTMLElement | undefined>();
  const restoreBackground = useRef<(() => void) | undefined>(undefined);
  const releaseBackground = useCallback(() => {
    restoreBackground.current?.();
    restoreBackground.current = undefined;
  }, []);
  const contentRef = useCallback((node: HTMLDivElement | null) => {
    releaseBackground();
    if (node) restoreBackground.current = isolateMenu(node);
  }, [releaseBackground]);
  useEffect(() => releaseBackground, [releaseBackground]);
  const options: Option[] = Children.toArray(children).flatMap(child => {
    if (!isValidElement<{ value?: string | number; children?: ReactNode; disabled?: boolean }>(child)) return [];
    const text = Children.toArray(child.props.children).join('');
    return [{ value: String(child.props.value ?? text), label: child.props.children, disabled: child.props.disabled }];
  });
  return <Menu.Root value={value == null ? undefined : String(value) || EMPTY} defaultValue={defaultValue == null ? undefined : String(defaultValue) || EMPTY} disabled={disabled} name={name} required={required} onOpenChange={open => { if (open) setContainer(trigger.current?.closest('dialog') ?? undefined); else releaseBackground(); }} onValueChange={next => {
    const selected = next === EMPTY ? '' : next;
    onChange?.({ target: { value: selected }, currentTarget: { value: selected } } as React.ChangeEvent<HTMLSelectElement>);
  }}>
    <Menu.Trigger ref={node => { trigger.current = node; if (typeof forwardedRef === 'function') forwardedRef(node); else if (forwardedRef) forwardedRef.current = node; }} id={id} className={`select-trigger ${className ?? ''}`} aria-label={ariaLabel} aria-labelledby={labelledBy} aria-describedby={describedBy} title={title}>
      <Menu.Value>{selectedLabel}</Menu.Value><Menu.Icon className="select-chevron"><ChevronDown size={16} aria-hidden="true" /></Menu.Icon>
    </Menu.Trigger>
    <Menu.Portal container={container}><Menu.Content ref={contentRef} onCloseAutoFocus={releaseBackground} className="select-menu" position="popper" sideOffset={6} collisionPadding={12} align="start"><Menu.Viewport className="select-options">{options.map(option => <Menu.Item className="select-option" data-select-value={option.value} key={option.value || EMPTY} value={option.value || EMPTY} disabled={option.disabled}><Menu.ItemText>{option.label}</Menu.ItemText><Menu.ItemIndicator className="select-check"><Check size={16} /></Menu.ItemIndicator></Menu.Item>)}</Menu.Viewport></Menu.Content></Menu.Portal>
  </Menu.Root>;
});
export default Select;
