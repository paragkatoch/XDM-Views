import { useEffect, useRef } from "react";

export function Modal({ title, subtitle, size = "md", onClose, footer, children }) {
	const panelRef = useRef(null);
	const closeRef = useRef(onClose);
	closeRef.current = onClose;

	useEffect(() => {
		function onKey(event) {
			if (event.key === "Escape") {
				closeRef.current();
			}
		}
		document.addEventListener("keydown", onKey);
		const previousOverflow = document.body.style.overflow;
		document.body.style.overflow = "hidden";
		panelRef.current?.focus();
		return () => {
			document.removeEventListener("keydown", onKey);
			document.body.style.overflow = previousOverflow;
		};
	}, []);

	return (
		<div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
			<div className={`modal modal--${size}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={panelRef}>
				<header className="modal__head">
					<div>
						<h2>{title}</h2>
						{subtitle ? <p>{subtitle}</p> : null}
					</div>
					<button type="button" className="modal__close" onClick={onClose} aria-label="Close">
						×
					</button>
				</header>
				<div className="modal__body">{children}</div>
				{footer ? <footer className="modal__foot">{footer}</footer> : null}
			</div>
		</div>
	);
}
