import { Children, forwardRef, isValidElement, useRef, useState } from 'react';
import type { ReactNode, SelectHTMLAttributes } from 'react';
import * as Menu from '@radix-ui/react-select';
import { Check, ChevronDown } from 'lucide-react';
import './Select.css';

type Props = SelectHTMLAttributes<HTMLSelectElement>;
type Option = { value: string; label: ReactNode; disabled?: boolean };
const EMPTY = '__cqa_empty_option__';

/** Shared keyboard-accessible select; menu stays inside a modal when needed. */
const Select = forwardRef<HTMLButtonElement, Props>(function Select({ children, value, defaultValue, onChange, disabled, name, id, className, title, required, 'aria-label': ariaLabel, 'aria-labelledby': labelledBy, 'aria-describedby': describedBy }, forwardedRef) {
  const trigger = useRef<HTMLButtonElement | null>(null);
  const [container, setContainer] = useState<HTMLElement | undefined>();
  const options: Option[] = Children.toArray(children).flatMap(child => {
    if (!isValidElement<{ value?: string | number; children?: ReactNode; disabled?: boolean }>(child)) return [];
    const text = Children.toArray(child.props.children).join('');
    return [{ value: String(child.props.value ?? text), label: child.props.children, disabled: child.props.disabled }];
  });
  return <Menu.Root value={value == null ? undefined : String(value) || EMPTY} defaultValue={defaultValue == null ? undefined : String(defaultValue) || EMPTY} disabled={disabled} name={name} required={required} onOpenChange={open => { if (open) setContainer(trigger.current?.closest('dialog') ?? undefined); }} onValueChange={next => {
    const selected = next === EMPTY ? '' : next;
    onChange?.({ target: { value: selected }, currentTarget: { value: selected } } as React.ChangeEvent<HTMLSelectElement>);
  }}>
    <Menu.Trigger ref={node => { trigger.current = node; if (typeof forwardedRef === 'function') forwardedRef(node); else if (forwardedRef) forwardedRef.current = node; }} id={id} className={`select-trigger ${className ?? ''}`} aria-label={ariaLabel} aria-labelledby={labelledBy} aria-describedby={describedBy} title={title}>
      <Menu.Value /><Menu.Icon className="select-chevron"><ChevronDown size={16} /></Menu.Icon>
    </Menu.Trigger>
    <Menu.Portal container={container}><Menu.Content className="select-menu" position="popper" sideOffset={6} collisionPadding={12} align="start"><Menu.Viewport className="select-options">{options.map(option => <Menu.Item className="select-option" key={option.value || EMPTY} value={option.value || EMPTY} disabled={option.disabled}><Menu.ItemText>{option.label}</Menu.ItemText><Menu.ItemIndicator className="select-check"><Check size={16} /></Menu.ItemIndicator></Menu.Item>)}</Menu.Viewport></Menu.Content></Menu.Portal>
  </Menu.Root>;
});
export default Select;
