import s from '../ui.module.css'

export function CodeBlock({ text }: { text: string }) {
  return <pre className={s.code}>{text}</pre>
}

export function JsonBlock({ value }: { value: unknown }) {
  return <CodeBlock text={JSON.stringify(value ?? {}, null, 2)} />
}
