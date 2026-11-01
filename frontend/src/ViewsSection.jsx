import { useState } from "react";
import {
	JOIN_OPTIONS,
	PARAMETER_TYPE_LABELS,
	formatEntityName,
	formatFilterCondition,
	formatParameterLabel,
	joinDescription,
	orderRelationships,
} from "./format";
import { Modal } from "./Modal";
import { FILTER_OPERATORS, Menu, XmlEditor, conditionText, formatRefreshed, plural, readFileText, sourceClass } from "./sectionShared";
import { joinsOutdated, setColumns, toggleColumn, toggleEntity, withEntities } from "./viewEditing";

const TABS = [
	{ id: "entities", label: "Entities and columns" },
	{ id: "joins", label: "Joins" },
	{ id: "filters", label: "Filters" },
	{ id: "parameters", label: "Parameters" },
	{ id: "shape", label: "Grouping and sort" },
];

function viewListMeta(view) {
	const entities = (view.baseEntities ?? []).length;
	if (!entities) {
		return "No entities yet";
	}
	const parts = [plural(entities, "entity", "entities")];
	const joins = (view.relationshipRefs ?? []).length;
	const filters = (view.filters ?? []).length;
	if (joins) {
		parts.push(plural(joins, "join"));
	}
	if (filters) {
		parts.push(plural(filters, "filter"));
	}
	if ((view.groupBy ?? []).length) {
		parts.push("grouped");
	}
	return parts.join(" · ");
}

function describeView(view, relationships) {
	const entities = view.baseEntities ?? [];
	if (!entities.length) {
		return "This view is empty. Click Edit view to add entities.";
	}
	const sentences = [`Reads ${entities.map(formatEntityName).join(", ")}.`];
	for (const ref of view.relationshipRefs ?? []) {
		const condition = conditionText(relationships.find((item) => item.name === ref.name));
		sentences.push(`${formatParameterLabel(String(ref.type || "INNER").toLowerCase())} join${condition ? ` on ${condition}` : ""}.`);
	}
	if ((view.filters ?? []).length) {
		sentences.push(`Keeps rows where ${view.filters.map((filter) => formatFilterCondition(filter)).join(" and ")}.`);
	}
	if ((view.groupBy ?? []).length) {
		const measures = (view.measures ?? []).map((measure) => measure.alias).join(", ");
		sentences.push(`Groups by ${view.groupBy.map((item) => item.attribute).join(", ")}${measures ? ` and calculates ${measures}` : ""}.`);
	}
	if ((view.orderBy ?? []).length) {
		const sort = view.orderBy[0];
		sentences.push(`Sorts by ${sort.alias || sort.attribute} ${sort.direction || "asc"}.`);
	}
	return sentences.join(" ");
}

function shapeText(view) {
	const parts = [];
	if ((view.groupBy ?? []).length) {
		parts.push(`Group by ${view.groupBy.map((item) => item.attribute).join(", ")}`);
	}
	if ((view.measures ?? []).length) {
		parts.push(view.measures.map((measure) => `${measure.fn}(${measure.attribute || "*"}) as ${measure.alias}`).join(", "));
	}
	if ((view.having ?? []).length) {
		parts.push(`Keep groups where ${view.having.map((item) => `${item.alias} ${item.operator || "="} ${item.value}`).join(" and ")}`);
	}
	if ((view.orderBy ?? []).length) {
		parts.push(`Sort ${view.orderBy.map((sort) => `${sort.alias || sort.attribute} ${sort.direction || "asc"}`).join(", ")}`);
	}
	parts.push(view.limit ? `${view.limit} rows per page` : "All rows");
	return parts.join(" · ");
}

function Fact({ label, onEdit, children }) {
	return (
		<div className="fact">
			<dt>
				{label}
				<button type="button" className="link-button" onClick={onEdit}>
					Edit
				</button>
			</dt>
			<dd>{children}</dd>
		</div>
	);
}

function ViewSummary({ view, relationships, onEdit }) {
	const filters = view.filters ?? [];
	const parameters = view.parameters ?? [];
	const refs = view.relationshipRefs ?? [];

	return (
		<dl className="fact-list">
			<Fact label="Entities and columns" onEdit={() => onEdit("entities")}>
				{(view.baseEntities ?? []).length ? (
					(view.baseEntities ?? []).map((name) => (
						<p key={name}>
							<strong>{formatEntityName(name)}</strong> <span className="muted-text">{(view.projection?.[name] ?? []).join(", ")}</span>
						</p>
					))
				) : (
					<span className="muted-text">None yet</span>
				)}
			</Fact>
			<Fact label="Joins" onEdit={() => onEdit("joins")}>
				{refs.length ? (
					refs.map((ref) => {
						const relationship = relationships.find((item) => item.name === ref.name);
						return (
							<p key={ref.name}>
								{formatEntityName(relationship?.left_entity)} <span className="join-pill">{formatParameterLabel(String(ref.type || "INNER").toLowerCase())}</span>{" "}
								{formatEntityName(relationship?.right_entity)} <code className="muted-text">{conditionText(relationship)}</code>
							</p>
						);
					})
				) : (
					<span className="muted-text">None</span>
				)}
			</Fact>
			<Fact label="Filters" onEdit={() => onEdit("filters")}>
				{filters.length ? (
					filters.map((filter, index) => (
						<p key={index}>
							<strong>{formatEntityName(filter.entity)}</strong> <code>{formatFilterCondition(filter)}</code>
						</p>
					))
				) : (
					<span className="muted-text">None, every row is kept</span>
				)}
			</Fact>
			<Fact label="Parameters" onEdit={() => onEdit("parameters")}>
				{parameters.length ? (
					parameters.map((param) => (
						<p key={param.name}>
							<code>${param.name}</code>{" "}
							<span className="muted-text">
								{PARAMETER_TYPE_LABELS[param.type] ?? param.type}
								{param.default !== "" && param.default != null ? `, default ${param.default}` : ""}
							</span>
						</p>
					))
				) : (
					<span className="muted-text">None</span>
				)}
			</Fact>
			<Fact label="Grouping and sort" onEdit={() => onEdit("shape")}>
				{shapeText(view)}
			</Fact>
		</dl>
	);
}

function EntitiesTab({ draft, entities, relationships, setDraft }) {
	const entityByName = new Map(entities.map((entity) => [entity.name, entity]));
	const selected = draft.baseEntities ?? [];
	const unused = entities.filter((entity) => !selected.includes(entity.name));

	return (
		<>
			{selected.length ? (
				<div className="picked-entities">
					{selected.map((name) => {
						const entity = entityByName.get(name);
						const available = entity?.attributes ?? [];
						const picked = draft.projection?.[name] ?? available;
						return (
							<div className="picked-entity" key={name}>
								<div className="picked-entity__head">
									<strong>{formatEntityName(name)}</strong>
									<span className={sourceClass(entity?.sourceType)}>{entity?.sourceType || "Source"}</span>
									<span className="picked-entity__count">{picked.length} of {available.length}</span>
									<button className="link-button" onClick={() => setDraft(setColumns(draft, name, available))}>
										All
									</button>
									<button className="link-button link-button--danger" onClick={() => setDraft(toggleEntity(draft, name, entities, relationships))}>
										Remove
									</button>
								</div>
								<div className="chip-row">
									{available.map((attribute) => {
										const on = picked.includes(attribute);
										return (
											<button
												type="button"
												key={attribute}
												aria-pressed={on}
												className={on ? "column-chip column-chip--on" : "column-chip"}
												onClick={() => setDraft(toggleColumn(draft, name, attribute, entities))}
											>
												{formatParameterLabel(attribute)}
											</button>
										);
									})}
								</div>
							</div>
						);
					})}
				</div>
			) : (
				<p className="muted-text">No entities yet. Add one below.</p>
			)}
			{unused.length ? (
				<div className="add-row">
					<span>Add</span>
					{unused.map((entity) => (
						<button
							type="button"
							key={entity.name}
							className="button button--secondary button--small"
							onClick={() => setDraft(toggleEntity(draft, entity.name, entities, relationships))}
						>
							+ {formatEntityName(entity.name)}
						</button>
					))}
				</div>
			) : null}
		</>
	);
}

function JoinsTab({ draft, entities, relationships, setDraft }) {
	const link = orderRelationships(draft.baseEntities ?? [], relationships);
	const refs = draft.relationshipRefs ?? [];

	return (
		<>
			<p className="muted-text tab-hint">Joins come from the relationships in section 1. Pick how each one matches rows.</p>
			{link.missing.length ? (
				<p className="builder-warning">
					No relationship connects {link.missing.map(formatEntityName).join(", ")}. Remove it, or add a relationship in section 1.
				</p>
			) : null}
			{joinsOutdated(draft, relationships) ? (
				<div className="relink-note">
					<span>Section 1 has relationships that connect these entities, but this view does not use them yet.</span>
					<button className="button button--small" onClick={() => setDraft(withEntities(draft, draft.baseEntities ?? [], entities, relationships))}>
						Use these joins
					</button>
				</div>
			) : null}
			{refs.length ? (
				<div className="join-list">
					{refs.map((ref, index) => {
						const relationship = relationships.find((item) => item.name === ref.name);
						const type = ref.type || "INNER";
						return (
							<div className="join-row" key={ref.name}>
								<span className="join-row__entity">{formatEntityName(relationship?.left_entity)}</span>
								<select
									aria-label={`Join type for ${ref.name}`}
									value={type}
									onChange={(event) =>
										setDraft({
											...draft,
											relationshipRefs: refs.map((item, itemIndex) => (itemIndex === index ? { ...item, type: event.target.value } : item)),
										})
									}
								>
									{JOIN_OPTIONS.map((option) => (
										<option key={option.value} value={option.value}>
											{option.label} join
										</option>
									))}
								</select>
								<span className="join-row__entity">{formatEntityName(relationship?.right_entity)}</span>
								<p className="join-row__detail">
									<code>{conditionText(relationship)}</code>
									<span>{joinDescription(type)}</span>
								</p>
							</div>
						);
					})}
				</div>
			) : (
				<p className="muted-text">{(draft.baseEntities ?? []).length > 1 ? "These entities have no relationship to join on." : "Add a second entity to join it."}</p>
			)}
		</>
	);
}

function FiltersTab({ draft, entities, setDraft }) {
	const entityByName = new Map(entities.map((entity) => [entity.name, entity]));
	const filters = draft.filters ?? [];
	const open = (draft.baseEntities ?? []).filter((name) => !filters.some((filter) => filter.entity === name));
	const update = (index, patch) =>
		setDraft({ ...draft, filters: filters.map((filter, itemIndex) => (itemIndex === index ? { ...filter, ...patch } : filter)) });

	return (
		<>
			<p className="muted-text tab-hint">One filter per entity. Write $name in a value to use a parameter.</p>
			{filters.length ? (
				<div className="filter-list">
					{filters.map((filter, index) => {
						const entity = entityByName.get(filter.entity);
						return (
							<div className="filter-row" key={`${filter.entity}-${index}`}>
								<span className="filter-row__entity">{formatEntityName(filter.entity)}</span>
								{filter.query ? (
									<label className="field filter-row__query">
										<span>XPath</span>
										<input value={filter.query} onChange={(event) => update(index, { query: event.target.value })} />
									</label>
								) : (
									<>
										<label className="field">
											<span>Column</span>
											<select value={filter.attribute ?? ""} onChange={(event) => update(index, { attribute: event.target.value })}>
												{(entity?.attributes ?? []).map((attribute) => (
													<option key={attribute} value={attribute}>
														{attribute}
													</option>
												))}
											</select>
										</label>
										<label className="field field--narrow">
											<span>Operator</span>
											<select value={filter.operator || "="} onChange={(event) => update(index, { operator: event.target.value })}>
												{FILTER_OPERATORS.map((operator) => (
													<option key={operator} value={operator}>
														{operator}
													</option>
												))}
											</select>
										</label>
										<label className="field">
											<span>Value</span>
											<input value={filter.value ?? ""} placeholder="New York or $city" onChange={(event) => update(index, { value: event.target.value })} />
										</label>
									</>
								)}
								<button
									className="link-button link-button--danger"
									onClick={() => setDraft({ ...draft, filters: filters.filter((_, itemIndex) => itemIndex !== index) })}
								>
									Remove
								</button>
							</div>
						);
					})}
				</div>
			) : (
				<p className="muted-text">No filters. Every row is kept.</p>
			)}
			{open.length ? (
				<div className="add-row">
					<span>Add filter on</span>
					{open.map((name) => (
						<button
							type="button"
							key={name}
							className="button button--secondary button--small"
							onClick={() =>
								setDraft({
									...draft,
									filters: [...filters, { entity: name, attribute: entityByName.get(name)?.attributes?.[0] ?? "", operator: "=", value: "", query: null }],
								})
							}
						>
							+ {formatEntityName(name)}
						</button>
					))}
				</div>
			) : null}
		</>
	);
}

function ParametersTab({ draft, setDraft }) {
	const parameters = draft.parameters ?? [];
	const update = (index, patch) =>
		setDraft({ ...draft, parameters: parameters.map((param, itemIndex) => (itemIndex === index ? { ...param, ...patch } : param)) });

	function add() {
		const used = new Set(parameters.map((param) => param.name));
		let index = 1;
		while (used.has(`param_${index}`)) {
			index += 1;
		}
		setDraft({ ...draft, parameters: [...parameters, { name: `param_${index}`, type: "string", default: "" }] });
	}

	return (
		<>
			<p className="muted-text tab-hint">Values asked for in step 3. The default is used when nothing is entered.</p>
			{parameters.length ? (
				<div className="filter-list">
					{parameters.map((param, index) => (
						<div className="filter-row" key={index}>
							<label className="field">
								<span>Name</span>
								<input value={param.name} onChange={(event) => update(index, { name: event.target.value.replace(/[^A-Za-z0-9_]/g, "") })} />
							</label>
							<label className="field field--narrow">
								<span>Type</span>
								<select value={param.type || "string"} onChange={(event) => update(index, { type: event.target.value })}>
									{Object.entries(PARAMETER_TYPE_LABELS).map(([value, label]) => (
										<option key={value} value={value}>
											{label}
										</option>
									))}
								</select>
							</label>
							<label className="field">
								<span>Default</span>
								<input value={param.default ?? ""} onChange={(event) => update(index, { default: event.target.value })} />
							</label>
							<button
								className="link-button link-button--danger"
								onClick={() => setDraft({ ...draft, parameters: parameters.filter((_, itemIndex) => itemIndex !== index) })}
							>
								Remove
							</button>
						</div>
					))}
				</div>
			) : (
				<p className="muted-text">No parameters.</p>
			)}
			<div className="add-row">
				<button className="button button--secondary button--small" onClick={add}>
					+ Parameter
				</button>
			</div>
		</>
	);
}

function ShapeTab({ draft, setDraft, stepShape, stepShapeText }) {
	return (
		<>
			<p className="muted-text tab-hint">Set grouping, measures, sort, and page size in step 3, then copy them onto the view here.</p>
			<dl className="detail-list">
				<div>
					<dt>On this view</dt>
					<dd>{shapeText(draft)}</dd>
				</div>
				<div>
					<dt>Step 3 now</dt>
					<dd>{stepShapeText}</dd>
				</div>
			</dl>
			<div className="add-row">
				<button className="button button--secondary button--small" onClick={() => setDraft({ ...draft, ...stepShape })}>
					Use step 3 settings
				</button>
				<button
					className="button button--secondary button--small"
					onClick={() => setDraft({ ...draft, groupBy: [], measures: [], having: [], orderBy: [], limit: null, offset: 0 })}
				>
					Clear
				</button>
			</div>
		</>
	);
}

function EditViewDialog({ view, entities, relationships, initialTab, stepShape, stepShapeText, onApply, onClose }) {
	const [draft, setDraft] = useState(view);
	const [tab, setTab] = useState(initialTab || "entities");
	const [error, setError] = useState("");
	const changed = JSON.stringify(draft) !== JSON.stringify(view);
	const counts = {
		entities: (draft.baseEntities ?? []).length,
		joins: (draft.relationshipRefs ?? []).length,
		filters: (draft.filters ?? []).length,
		parameters: (draft.parameters ?? []).length,
	};

	function close() {
		if (!changed || window.confirm("Discard the changes to this view?")) {
			onClose();
		}
	}

	function apply() {
		const names = (draft.parameters ?? []).map((param) => param.name);
		if (names.some((name) => !name)) {
			setTab("parameters");
			setError("Every parameter needs a name.");
			return;
		}
		if (new Set(names).size !== names.length) {
			setTab("parameters");
			setError("Parameter names must be different.");
			return;
		}
		onApply(draft);
		onClose();
	}

	const tabProps = { draft, entities, relationships, setDraft: (next) => { setError(""); setDraft(next); } };

	return (
		<Modal
			title={`Edit ${view.name}`}
			subtitle="Changes stay in this dialog until you click Apply."
			size="xl"
			onClose={close}
			footer={(
				<>
					{error ? <span className="modal__error">{error}</span> : null}
					<span className="modal__spacer" />
					<button className="button button--secondary" onClick={close}>
						Cancel
					</button>
					<button className="button" onClick={apply} disabled={!changed}>
						Apply changes
					</button>
				</>
			)}
		>
			<div className="tabs" role="tablist">
				{TABS.map((item) => (
					<button
						type="button"
						role="tab"
						key={item.id}
						aria-selected={tab === item.id}
						className={tab === item.id ? "tab tab--active" : "tab"}
						onClick={() => setTab(item.id)}
					>
						{item.label}
						{counts[item.id] != null ? <span className="tab__count">{counts[item.id]}</span> : null}
					</button>
				))}
			</div>
			<div className="tab-panel">
				{tab === "entities" ? <EntitiesTab {...tabProps} /> : null}
				{tab === "joins" ? <JoinsTab {...tabProps} /> : null}
				{tab === "filters" ? <FiltersTab {...tabProps} /> : null}
				{tab === "parameters" ? <ParametersTab {...tabProps} /> : null}
				{tab === "shape" ? <ShapeTab {...tabProps} stepShape={stepShape} stepShapeText={stepShapeText} /> : null}
			</div>
		</Modal>
	);
}

function CreateViewDialog({ names, entities, relationships, onCreate, onClose }) {
	const [name, setName] = useState(() => {
		let candidate = "New view";
		let suffix = 2;
		while (names.includes(candidate)) {
			candidate = `New view ${suffix}`;
			suffix += 1;
		}
		return candidate;
	});
	const [picked, setPicked] = useState([]);
	const [error, setError] = useState("");
	const link = orderRelationships(picked, relationships);

	function create() {
		const problem = onCreate(name, picked);
		if (problem) {
			setError(problem);
		}
	}

	return (
		<Modal
			title="New view"
			subtitle="Name it and pick what it reads. You can change everything afterwards."
			onClose={onClose}
			footer={(
				<>
					<span className="modal__spacer" />
					<button className="button button--secondary" onClick={onClose}>
						Cancel
					</button>
					<button className="button" onClick={create}>
						Create and edit
					</button>
				</>
			)}
		>
			<label className="field">
				<span>Name</span>
				<input autoFocus value={name} onChange={(event) => setName(event.target.value)} onKeyDown={(event) => event.key === "Enter" && create()} />
			</label>
			<p className="label modal-label">Entities</p>
			<div className="pick-grid">
				{entities.map((entity) => {
					const on = picked.includes(entity.name);
					return (
						<button
							type="button"
							key={entity.name}
							aria-pressed={on}
							className={on ? "pick-tile pick-tile--on" : "pick-tile"}
							onClick={() => setPicked(on ? picked.filter((item) => item !== entity.name) : [...picked, entity.name])}
						>
							<strong>{formatEntityName(entity.name)}</strong>
							<span>{entity.materialized ? "Materialized" : entity.sourceType}</span>
						</button>
					);
				})}
			</div>
			{link.missing.length ? (
				<p className="builder-warning">No relationship connects {link.missing.map(formatEntityName).join(", ")}. Add one in section 1 or pick other entities.</p>
			) : link.joins.length ? (
				<p className="muted-text">Joined with {link.joins.map((item) => formatEntityName(item.name)).join(", ")}.</p>
			) : null}
			{error ? <p className="builder-warning">{error}</p> : null}
		</Modal>
	);
}

function RenameDialog({ view, onRename, onClose }) {
	const [name, setName] = useState(view.name);
	const [error, setError] = useState("");

	function save() {
		const problem = onRename(name);
		if (problem) {
			setError(problem);
			return;
		}
		onClose();
	}

	return (
		<Modal
			title="Rename view"
			onClose={onClose}
			size="sm"
			footer={(
				<>
					<span className="modal__spacer" />
					<button className="button button--secondary" onClick={onClose}>
						Cancel
					</button>
					<button className="button" onClick={save}>
						Rename
					</button>
				</>
			)}
		>
			<label className="field">
				<span>Name</span>
				<input autoFocus value={name} onChange={(event) => setName(event.target.value)} onKeyDown={(event) => event.key === "Enter" && save()} />
			</label>
			{error ? <p className="builder-warning">{error}</p> : null}
		</Modal>
	);
}

function XmlDialog({ title, subtitle, value, applying, onUpload, onApply, onClose }) {
	const [draft, setDraft] = useState(value);
	const [error, setError] = useState("");

	async function apply() {
		const problem = await onApply(draft);
		if (problem) {
			setError(problem);
			return;
		}
		onClose();
	}

	return (
		<Modal
			title={title}
			subtitle={subtitle}
			size="xl"
			onClose={() => (draft === value || window.confirm("Discard your XML changes?")) && onClose()}
			footer={(
				<>
					<button className="button button--secondary" onClick={() => setDraft(value)} disabled={draft === value}>
						Reset
					</button>
					<span className="modal__spacer" />
					<button className="button button--secondary" onClick={onClose}>
						Cancel
					</button>
					<button className="button" onClick={apply} disabled={applying || draft === value}>
						{applying ? "Applying..." : "Apply"}
					</button>
				</>
			)}
		>
			{error ? <p className="builder-warning">{error}</p> : null}
			<XmlEditor value={draft} onChange={setDraft} onUpload={onUpload ? (event) => readFileText(event, setDraft) : null} minRows={24} />
		</Modal>
	);
}

export function ViewsSection({
	summary,
	activeView,
	viewXml,
	viewsXml,
	dirty,
	saving,
	applying,
	stepShape,
	stepShapeText,
	materializing,
	materializeBlocked,
	onSelect,
	onCreate,
	onRename,
	onDelete,
	onChange,
	onApplyViewXml,
	onApplyAllXml,
	onSaveFile,
	onMaterialize,
}) {
	const [dialog, setDialog] = useState(null);
	const views = summary?.views ?? [];
	const entities = summary?.entities ?? [];
	const relationships = summary?.relationships ?? [];
	const close = () => setDialog(null);
	const storedAs = entities.find((entity) => entity.materialized?.viewName === activeView?.name);
	const link = activeView ? orderRelationships(activeView.baseEntities ?? [], relationships) : { missing: [] };
	const outdated = activeView ? joinsOutdated(activeView, relationships) : false;
	const editReady = dialog?.kind === "edit" && activeView && (!dialog.pending || activeView.name === dialog.pending);

	return (
		<section className="panel" id="views">
			<div className="section-header">
				<div>
					<p className="label">Section 2 · Views</p>
					<h2>Views</h2>
					<p className="section-description">Pick a view to see what it contains. Edit opens the full editor.</p>
				</div>
				<div className="button-row button-row--center">
					{dirty ? <span className="unsaved-badge">Not saved to views.xml</span> : null}
					<button className="button button--secondary" onClick={() => setDialog({ kind: "allXml" })}>
						All views XML
					</button>
					<button className="button" onClick={onSaveFile} disabled={!dirty || saving}>
						{saving ? "Saving..." : "Save to views.xml"}
					</button>
				</div>
			</div>

			<div className="views-layout">
				<aside className="view-list" aria-label="Views">
					<button className="button view-list__new" onClick={() => setDialog({ kind: "create" })} disabled={!entities.length}>
						+ New view
					</button>
					<div className="view-list__items">
						{views.map((view) => (
							<button
								type="button"
								key={view.name}
								className={view.name === activeView?.name ? "view-list__item view-list__item--active" : "view-list__item"}
								onClick={() => onSelect(view.name)}
							>
								<strong>{view.name}</strong>
								<span>{viewListMeta(view)}</span>
							</button>
						))}
					</div>
				</aside>

				<div className="view-detail">
					{!activeView ? (
						<div className="empty-box">Choose a view on the left, or create a new one.</div>
					) : (
						<>
							<header className="view-detail__head">
								<div>
									<h3>{activeView.name}</h3>
									<p>{describeView(activeView, relationships)}</p>
								</div>
								<div className="button-row">
									<button className="button" onClick={() => setDialog({ kind: "edit", tab: "entities" })}>
										Edit view
									</button>
									<button
										className="button button--secondary"
										onClick={onMaterialize}
										disabled={Boolean(materializing) || Boolean(materializeBlocked)}
										title={materializeBlocked || "Store the full result as a table other views can use"}
									>
										{materializing === activeView.name ? "Storing..." : storedAs ? "Refresh stored result" : "Materialize"}
									</button>
									<Menu
										items={[
											{ label: "View XML", onClick: () => setDialog({ kind: "viewXml" }) },
											{ label: "Rename", onClick: () => setDialog({ kind: "rename" }) },
											{ label: "Delete view", danger: true, onClick: onDelete },
										]}
									/>
								</div>
							</header>

							{link.missing.length || outdated ? (
								<div className="relink-note">
									<span>
										{link.missing.length
											? `No relationship connects ${link.missing.map(formatEntityName).join(", ")}.`
											: "New relationships in section 1 connect these entities, but this view does not use them yet."}
									</span>
									<button className="button button--small" onClick={() => setDialog({ kind: "edit", tab: "joins" })}>
										Fix joins
									</button>
								</div>
							) : null}
							{storedAs ? (
								<p className="status-box status-box--materialized">
									Materialized as <strong>{formatEntityName(storedAs.name)}</strong>: {plural(storedAs.materialized.rowCount, "row")}, refreshed{" "}
									{formatRefreshed(storedAs.materialized.refreshedAt)}.
								</p>
							) : null}

							<ViewSummary view={activeView} relationships={relationships} onEdit={(tab) => setDialog({ kind: "edit", tab })} />
						</>
					)}
				</div>
			</div>

			{editReady ? (
				<EditViewDialog
					key={activeView.name}
					view={activeView}
					entities={entities}
					relationships={relationships}
					initialTab={dialog.tab}
					stepShape={stepShape}
					stepShapeText={stepShapeText}
					onApply={onChange}
					onClose={close}
				/>
			) : null}
			{dialog?.kind === "create" ? (
				<CreateViewDialog
					names={summary?.viewNames ?? []}
					entities={entities}
					relationships={relationships}
					onCreate={(name, picked) => {
						const problem = onCreate(name, picked);
						if (!problem) {
							setDialog({ kind: "edit", tab: "entities", pending: name.trim() });
						}
						return problem;
					}}
					onClose={close}
				/>
			) : null}
			{dialog?.kind === "rename" && activeView ? <RenameDialog view={activeView} onRename={onRename} onClose={close} /> : null}
			{dialog?.kind === "viewXml" && activeView ? (
				<XmlDialog
					title={`${activeView.name} XML`}
					subtitle="Only this view. Apply replaces it in views.xml."
					value={viewXml}
					applying={applying}
					onApply={onApplyViewXml}
					onClose={close}
				/>
			) : null}
			{dialog?.kind === "allXml" ? (
				<XmlDialog
					title="views.xml"
					subtitle="Every view in one file. Apply checks it and reloads the list."
					value={viewsXml}
					applying={applying}
					onUpload
					onApply={onApplyAllXml}
					onClose={close}
				/>
			) : null}
		</section>
	);
}
