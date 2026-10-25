import { useEffect, useRef, useState } from "react";
import { formatEntityName } from "./format";

export const FILTER_OPERATORS = ["=", "!=", ">", "<", ">=", "<="];

export function sourceClass(type) {
	return `source-badge source-badge--${String(type || "source").toLowerCase()}`;
}

export function plural(count, word, many = `${word}s`) {
	return `${count} ${count === 1 ? word : many}`;
}

export function formatRefreshed(value) {
	const date = new Date(value);
	return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function conditionText(relationship) {
	const condition = relationship?.condition;
	if (!condition) {
		return "";
	}
	return `${formatEntityName(condition.left_entity)}.${condition.left_attribute} ${condition.operator || "="} ${formatEntityName(condition.right_entity)}.${condition.right_attribute}`;
}

export function readFileText(event, onText) {
	const file = event.target.files?.[0];
	if (file) {
		file.text().then(onText);
	}
	event.target.value = "";
}

export function XmlEditor({ value, onChange, onUpload, minRows = 18 }) {
	return (
		<div className="xml-editor">
			{onUpload ? (
				<div className="xml-editor__tools">
					<label className="button button--secondary button--small">
						<input type="file" accept=".xml,text/xml,application/xml" onChange={onUpload} />
						Upload XML
					</label>
				</div>
			) : null}
			<textarea spellCheck="false" rows={minRows} value={value} onChange={(event) => onChange(event.target.value)} />
			<div className="editor-footer">
				<span>{value.split("\n").length} lines</span>
				<span>XML</span>
			</div>
		</div>
	);
}

export function Menu({ label = "More actions", items }) {
	const [open, setOpen] = useState(false);
	const ref = useRef(null);

	useEffect(() => {
		if (!open) {
			return undefined;
		}
		function onDown(event) {
			if (!ref.current?.contains(event.target)) {
				setOpen(false);
			}
		}
		document.addEventListener("mousedown", onDown);
		return () => document.removeEventListener("mousedown", onDown);
	}, [open]);

	return (
		<div className="menu" ref={ref}>
			<button type="button" className="button button--secondary menu__trigger" aria-label={label} aria-expanded={open} onClick={() => setOpen(!open)}>
				⋯
			</button>
			{open ? (
				<div className="menu__list" role="menu">
					{items.map((item) => (
						<button
							type="button"
							role="menuitem"
							key={item.label}
							className={item.danger ? "menu__item menu__item--danger" : "menu__item"}
							onClick={() => {
								setOpen(false);
								item.onClick();
							}}
						>
							{item.label}
						</button>
					))}
				</div>
			) : null}
		</div>
	);
}
