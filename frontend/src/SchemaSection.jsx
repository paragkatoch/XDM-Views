import { useState } from "react";
import { formatEntityName } from "./format";
import { removeRelationshipXml, replaceRelationshipXml } from "./metaschemaDocument";
import { Modal } from "./Modal";
import { XmlEditor, conditionText, formatRefreshed, plural, readFileText, sourceClass } from "./sectionShared";

function connectionTarget(connection) {
	return connection?.url || connection?.path || "";
}

function maskSecret(value) {
	if (!value) {
		return "";
	}
	return /^\$\{[^}]+\}$/.test(value) ? value : "••••••";
}

function usedByViews(views, predicate) {
	return views.filter(predicate).map((view) => view.name);
}

function DetailList({ rows }) {
	return (
		<dl className="detail-list">
			{rows
				.filter((row) => row.value)
				.map((row) => (
					<div key={row.label}>
						<dt>{row.label}</dt>
						<dd>{row.value}</dd>
					</div>
				))}
		</dl>
	);
}

function SourceDialog({ database, entities, result, onTest, onClose }) {
	const connection = database.connection;
	const members = entities.filter((entity) => entity.databaseRef === database.id);

	return (
		<Modal
			title={database.name || database.id}
			subtitle={`Data source ${database.id} · ${database.type}`}
			onClose={onClose}
			footer={(
				<>
					<button className="button button--secondary" onClick={onClose}>
						Close
					</button>
					<button className="button" onClick={onTest} disabled={result?.pending}>
						{result?.pending ? "Testing..." : "Test connection"}
					</button>
				</>
			)}
		>
			{connection ? (
				<DetailList
					rows={[
						{ label: database.type === "XML" ? "File" : "URL", value: <code>{connectionTarget(connection)}</code> },
						{ label: "Username", value: connection.username ? <code>{connection.username}</code> : "" },
						{ label: "Password", value: connection.password ? <code>{maskSecret(connection.password)}</code> : "" },
						{ label: "Entities", value: members.map((entity) => formatEntityName(entity.name)).join(", ") || "None" },
					]}
				/>
			) : (
				<p className="builder-warning">
					No connection is set. Add <code>&lt;Connection url="sqlite:///file.db"/&gt;</code> (or <code>path="file.xml"</code> for XML) to this database in the MetaSchema XML.
				</p>
			)}
			<p className="muted-text modal-note">
				Write <code>{"${NAME}"}</code> in the MetaSchema for hosts, users, or passwords. The value is read from <code>.env</code>.
			</p>
			{result && !result.pending ? (
				<p className={result.ok ? "status-box status-box--ok" : "status-box status-box--error"}>{result.message}</p>
			) : null}
		</Modal>
	);
}

function EntityDialog({ entity, views, materializing, onRefresh, onDrop, onClose }) {
	const details = entity.attributeDetails ?? entity.attributes.map((name) => ({ name }));
	const users = usedByViews(views, (view) => (view.baseEntities ?? []).includes(entity.name));
	const stored = entity.materialized;
	const isXml = entity.sourceType === "XML";

	return (
		<Modal
			title={formatEntityName(entity.name)}
			subtitle={`${entity.databaseName || entity.databaseRef} · ${stored ? "Materialized view" : entity.sourceType}`}
			onClose={onClose}
			footer={(
				<>
					{stored ? (
						<button className="button button--secondary button--danger" onClick={() => onDrop(entity.name)} disabled={Boolean(materializing)}>
							Remove stored result
						</button>
					) : null}
					<span className="modal__spacer" />
					{stored ? (
						<button className="button button--secondary" onClick={() => onRefresh(stored.viewName)} disabled={Boolean(materializing)}>
							{materializing === stored.viewName ? "Refreshing..." : "Refresh"}
						</button>
					) : null}
					<button className="button" onClick={onClose}>
						Close
					</button>
				</>
			)}
		>
			{stored ? (
				<p className="status-box status-box--materialized">
					Stored result of <strong>{stored.viewName}</strong>: {plural(stored.rowCount, "row")}, refreshed {formatRefreshed(stored.refreshedAt)}.
				</p>
			) : null}
			<DetailList
				rows={[
					{ label: "Base path", value: entity.basePath ? <code>{entity.basePath}</code> : "" },
					{ label: "Used by", value: users.length ? users.join(", ") : "No views yet" },
				]}
			/>
			<table className="column-table">
				<thead>
					<tr>
						<th>Column</th>
						<th>Type</th>
						{isXml ? <th>XML path</th> : null}
					</tr>
				</thead>
				<tbody>
					{details.map((attribute) => (
						<tr key={attribute.name}>
							<td>
								<code>{attribute.name}</code>
								{attribute.key === "primary" ? <span className="key-badge">Key</span> : null}
							</td>
							<td>{attribute.type || ""}</td>
							{isXml ? <td><code>{attribute.path || ""}</code></td> : null}
						</tr>
					))}
				</tbody>
			</table>
		</Modal>
	);
}

function suggestedRelationshipName(draft) {
	return draft.leftEntity && draft.rightEntity ? `${draft.leftEntity}${draft.rightEntity}Join` : "";
}

function RelationshipDialog({ relationship, entities, relationships, views, metaschemaXml, saving, onSave, onClose }) {
	const previous = relationship?.name ?? "";
	const users = previous ? usedByViews(views, (view) => (view.relationshipRefs ?? []).some((ref) => ref.name === previous)) : [];
	const locked = users.length > 0;
	const [draft, setDraft] = useState(() => (relationship
		? {
			name: relationship.name,
			leftEntity: relationship.left_entity,
			leftAttribute: relationship.condition?.left_attribute ?? "",
			rightEntity: relationship.right_entity,
			rightAttribute: relationship.condition?.right_attribute ?? "",
			nameTouched: true,
		}
		: { name: "", leftEntity: "", leftAttribute: "", rightEntity: "", rightAttribute: "", nameTouched: false }));
	const [error, setError] = useState("");

	const entityByName = new Map(entities.map((entity) => [entity.name, entity]));
	const attributeType = (entityName, attribute) =>
		entityByName.get(entityName)?.attributeDetails?.find((item) => item.name === attribute)?.type;
	const leftType = attributeType(draft.leftEntity, draft.leftAttribute);
	const rightType = attributeType(draft.rightEntity, draft.rightAttribute);

	function change(patch) {
		setDraft((current) => {
			const next = { ...current, ...patch };
			if (!next.nameTouched) {
				next.name = suggestedRelationshipName(next);
			}
			return next;
		});
	}

	function validate() {
		const name = draft.name.trim();
		if (!draft.leftEntity || !draft.rightEntity || !draft.leftAttribute || !draft.rightAttribute) {
			return "Pick an entity and a column on both sides.";
		}
		if (draft.leftEntity === draft.rightEntity) {
			return "Pick two different entities.";
		}
		if (!/^[A-Za-z_][\w-]*$/.test(name)) {
			return "Use letters, digits, - or _ in the name, starting with a letter.";
		}
		if (name !== previous && relationships.some((item) => item.name === name)) {
			return `A relationship named ${name} already exists.`;
		}
		return "";
	}

	async function save() {
		const problem = validate();
		if (problem) {
			setError(problem);
			return;
		}
		try {
			const nextXml = replaceRelationshipXml(metaschemaXml, { ...draft, name: draft.name.trim() }, previous);
			const saveError = await onSave(nextXml);
			if (saveError) {
				setError(saveError);
				return;
			}
			onClose();
		} catch (err) {
			setError(err.message);
		}
	}

	async function remove() {
		if (!window.confirm(`Delete relationship ${formatEntityName(previous)}?`)) {
			return;
		}
		const saveError = await onSave(removeRelationshipXml(metaschemaXml, previous));
		if (saveError) {
			setError(saveError);
			return;
		}
		onClose();
	}

	function side(prefix, label) {
		const entityKey = `${prefix}Entity`;
		const attributeKey = `${prefix}Attribute`;
		const attributes = entityByName.get(draft[entityKey])?.attributes ?? [];
		return (
			<div className="relationship-form__side">
				<label className="field">
					<span>{label} entity</span>
					<select value={draft[entityKey]} disabled={locked} onChange={(event) => change({ [entityKey]: event.target.value, [attributeKey]: "" })}>
						<option value="">Choose…</option>
						{entities.map((entity) => (
							<option key={entity.name} value={entity.name}>
								{formatEntityName(entity.name)}
							</option>
						))}
					</select>
				</label>
				<label className="field">
					<span>Column</span>
					<select value={draft[attributeKey]} disabled={!draft[entityKey]} onChange={(event) => change({ [attributeKey]: event.target.value })}>
						<option value="">Choose…</option>
						{attributes.map((attribute) => (
							<option key={attribute} value={attribute}>
								{attribute}
							</option>
						))}
					</select>
				</label>
			</div>
		);
	}

	return (
		<Modal
			title={previous ? `Edit ${formatEntityName(previous)}` : "New relationship"}
			subtitle="Which columns match between two entities. Views use this as a join."
			size="lg"
			onClose={onClose}
			footer={(
				<>
					{previous ? (
						<button
							className="button button--secondary button--danger"
							onClick={remove}
							disabled={saving || locked}
							title={locked ? `Used by ${users.join(", ")}` : undefined}
						>
							Delete
						</button>
					) : null}
					<span className="modal__spacer" />
					<button className="button button--secondary" onClick={onClose} disabled={saving}>
						Cancel
					</button>
					<button className="button" onClick={save} disabled={saving}>
						{saving ? "Saving..." : "Save relationship"}
					</button>
				</>
			)}
		>
			<div className="relationship-form">
				<div className="relationship-form__grid">
					{side("left", "Left")}
					<span className="relationship-form__equals" aria-hidden="true">=</span>
					{side("right", "Right")}
				</div>
				<label className="field relationship-form__name">
					<span>Name</span>
					<input value={draft.name} disabled={locked} onChange={(event) => change({ name: event.target.value, nameTouched: true })} />
				</label>
				{locked ? <p className="muted-text">Used by {users.join(", ")}, so only the columns can change and it can't be deleted.</p> : null}
				{leftType && rightType && leftType !== rightType ? (
					<p className="builder-warning">
						{draft.leftAttribute} is {leftType} and {draft.rightAttribute} is {rightType}. Rows only match when the values are equal.
					</p>
				) : null}
				{error ? <p className="builder-warning">{error}</p> : null}
				<p className="muted-text">Saved straight to MetaSchema.xml.</p>
			</div>
		</Modal>
	);
}

function MetaschemaDialog({ metaschemaXml, saving, onSave, onClose }) {
	const [draft, setDraft] = useState(metaschemaXml);
	const [error, setError] = useState("");

	async function save() {
		const saveError = await onSave(draft);
		if (saveError) {
			setError(saveError);
			return;
		}
		onClose();
	}

	return (
		<Modal
			title="MetaSchema XML"
			subtitle="Databases, connections, entities, and relationships. Save checks the XML and writes MetaSchema.xml."
			size="xl"
			onClose={() => (draft === metaschemaXml || window.confirm("Discard your XML changes?")) && onClose()}
			footer={(
				<>
					<button className="button button--secondary" onClick={() => setDraft(metaschemaXml)} disabled={draft === metaschemaXml}>
						Reset
					</button>
					<span className="modal__spacer" />
					<button className="button button--secondary" onClick={onClose}>
						Cancel
					</button>
					<button className="button" onClick={save} disabled={saving || draft === metaschemaXml}>
						{saving ? "Saving..." : "Save"}
					</button>
				</>
			)}
		>
			{error ? <p className="builder-warning">{error}</p> : null}
			<XmlEditor value={draft} onChange={setDraft} onUpload={(event) => readFileText(event, setDraft)} minRows={24} />
		</Modal>
	);
}

export function SchemaSection({
	summary,
	metaschemaXml,
	saving,
	onSaveMetaschema,
	onReload,
	onTestConnection,
	materializing,
	onRefreshMaterialized,
	onDropMaterialized,
}) {
	const [dialog, setDialog] = useState(null);
	const [connectionResults, setConnectionResults] = useState({});
	const databases = summary?.databases ?? [];
	const entities = summary?.entities ?? [];
	const relationships = summary?.relationships ?? [];
	const views = summary?.views ?? [];
	const close = () => setDialog(null);

	async function testConnection(id) {
		setConnectionResults((current) => ({ ...current, [id]: { pending: true } }));
		const result = await onTestConnection(id);
		setConnectionResults((current) => ({ ...current, [id]: result }));
	}

	function statusClass(id) {
		const result = connectionResults[id];
		if (!result || result.pending) {
			return "status-dot";
		}
		return result.ok ? "status-dot status-dot--ok" : "status-dot status-dot--error";
	}

	const openDatabase = dialog?.kind === "source" && databases.find((database) => database.id === dialog.id);
	const openEntity = dialog?.kind === "entity" && entities.find((entity) => entity.name === dialog.name);
	const openRelationship = dialog?.kind === "relationship" && dialog.name
		? relationships.find((relationship) => relationship.name === dialog.name)
		: null;

	return (
		<section className="panel" id="entities">
			<div className="section-header">
				<div>
					<p className="label">Section 1 · Entities</p>
					<h2>What the views can use</h2>
					<p className="section-description">Click anything to see its details. Everything here comes from <code>MetaSchema.xml</code>.</p>
				</div>
				<div className="button-row">
					<button className="button button--secondary" onClick={() => setDialog({ kind: "xml" })}>
						MetaSchema XML
					</button>
					<button className="button button--secondary" onClick={onReload}>
						Reload files
					</button>
				</div>
			</div>

			<div className="schema-block">
				<p className="label">Data sources</p>
				<div className="chip-list">
					{databases.map((database) => (
						<button type="button" className="source-chip" key={database.id} onClick={() => setDialog({ kind: "source", id: database.id })}>
							<span className={statusClass(database.id)} aria-hidden="true" />
							<strong>{database.name || database.id}</strong>
							<span className={sourceClass(database.type)}>{database.type}</span>
							{!database.connection ? <span className="source-chip__warn">No connection</span> : null}
						</button>
					))}
				</div>
			</div>

			<div className="schema-block">
				<p className="label">Entities</p>
				<div className="tile-grid">
					{entities.map((entity) => {
						const users = views.filter((view) => (view.baseEntities ?? []).includes(entity.name)).length;
						const stored = entity.materialized;
						return (
							<button type="button" className="entity-tile" key={entity.name} onClick={() => setDialog({ kind: "entity", name: entity.name })}>
								<span className="entity-tile__head">
									<strong>{formatEntityName(entity.name)}</strong>
									<span className={stored ? "source-badge source-badge--materialized" : sourceClass(entity.sourceType)}>
										{stored ? "Materialized" : entity.sourceType}
									</span>
								</span>
								<span className="entity-tile__meta">
									{plural(entity.attributes.length, "column")} · {plural(users, "view")}
									{stored ? ` · ${plural(stored.rowCount, "row")}` : ""}
								</span>
							</button>
						);
					})}
				</div>
			</div>

			<div className="schema-block">
				<div className="schema-block__head">
					<p className="label">Relationships</p>
					<button className="button button--secondary button--small" onClick={() => setDialog({ kind: "relationship", name: "" })} disabled={entities.length < 2}>
						+ Relationship
					</button>
				</div>
				{relationships.length ? (
					<div className="line-list">
						{relationships.map((relationship) => {
							const users = views.filter((view) => (view.relationshipRefs ?? []).some((ref) => ref.name === relationship.name)).length;
							return (
								<button
									type="button"
									className="relation-line"
									key={relationship.name}
									onClick={() => setDialog({ kind: "relationship", name: relationship.name })}
								>
									<span className="relation-line__pair">
										{formatEntityName(relationship.left_entity)} <span aria-hidden="true">↔</span> {formatEntityName(relationship.right_entity)}
									</span>
									<code>{conditionText(relationship)}</code>
									<span className="relation-line__meta">{plural(users, "view")}</span>
								</button>
							);
						})}
					</div>
				) : (
					<p className="muted-text">No relationships yet, so each view can only read one entity.</p>
				)}
			</div>

			{openDatabase ? (
				<SourceDialog
					database={openDatabase}
					entities={entities}
					result={connectionResults[openDatabase.id]}
					onTest={() => testConnection(openDatabase.id)}
					onClose={close}
				/>
			) : null}
			{openEntity ? (
				<EntityDialog
					entity={openEntity}
					views={views}
					materializing={materializing}
					onRefresh={onRefreshMaterialized}
					onDrop={async (name) => {
						if (await onDropMaterialized(name)) {
							close();
						}
					}}
					onClose={close}
				/>
			) : null}
			{dialog?.kind === "relationship" && (openRelationship || !dialog.name) ? (
				<RelationshipDialog
					key={dialog.name || "new"}
					relationship={openRelationship}
					entities={entities}
					relationships={relationships}
					views={views}
					metaschemaXml={metaschemaXml}
					saving={saving}
					onSave={onSaveMetaschema}
					onClose={close}
				/>
			) : null}
			{dialog?.kind === "xml" ? (
				<MetaschemaDialog metaschemaXml={metaschemaXml} saving={saving} onSave={onSaveMetaschema} onClose={close} />
			) : null}
		</section>
	);
}
