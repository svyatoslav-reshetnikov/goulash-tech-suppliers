import type { ReactNode } from "react";

const LINK_CLASS = "text-cyan-700 hover:underline break-words";

// сайт, email или российский телефон внутри текста
const TOKEN = /(https?:\/\/[^\s<>"«»]*[^\s<>"«».,;:!?)])|([\w.+-]+@[\w-]+(?:\.[\w-]+)+)|((?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s-]*\d{2}[\s-]*\d{2})/g;

/** Ссылка tel: из любого написания номера: +7 (843) 555-01-23, 8 800 555 35 35, 843 555-01-23. */
export function phoneHref(value: string): string | null {
  let digits = value.replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("8")) digits = "7" + digits.slice(1);
  if (digits.length === 10) digits = "7" + digits;
  return digits.length === 11 ? `tel:+${digits}` : null;
}

/** Текст, в котором сайты, email и телефоны стали ссылками (открываются, пишутся, набираются одним кликом). */
export function Linkified({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(TOKEN)) {
    const start = match.index ?? 0;
    if (start > last) parts.push(text.slice(last, start));
    const [token, url, email, phone] = match;
    if (url) {
      parts.push(
        <a key={start} href={url} target="_blank" rel="noreferrer" className={LINK_CLASS}>
          {token}
        </a>,
      );
    } else if (email) {
      parts.push(
        <a key={start} href={`mailto:${token}`} className={LINK_CLASS}>
          {token}
        </a>,
      );
    } else if (phone) {
      parts.push(
        <a key={start} href={phoneHref(token) ?? undefined} className={LINK_CLASS}>
          {token}
        </a>,
      );
    }
    last = start + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

/** Значение поля карточки с подходящей ссылкой: телефон — звонок, email — письмо, адрес — карта, остальное — ссылки внутри текста. */
export function FieldText({ field, value }: { field: string; value: string }) {
  if (field === "phone") {
    const href = phoneHref(value);
    const hasInline = new RegExp(TOKEN.source).test(value);
    return href && !hasInline ? (
      <a href={href} className={LINK_CLASS}>
        {value}
      </a>
    ) : (
      <Linkified text={value} />
    );
  }
  if (field === "email") {
    return /^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$/.test(value.trim()) ? (
      <a href={`mailto:${value.trim()}`} className={LINK_CLASS}>
        {value}
      </a>
    ) : (
      <Linkified text={value} />
    );
  }
  if (field === "address") {
    return (
      <a href={`https://yandex.ru/maps/?text=${encodeURIComponent(value)}`} target="_blank" rel="noreferrer" className={LINK_CLASS} title="Открыть на карте">
        {value}
      </a>
    );
  }
  return <Linkified text={value} />;
}
