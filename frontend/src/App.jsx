import { useEffect, useMemo, useState } from "react";
import {
	formatEntityName,
	formatFilterCondition,
	formatParameterHint,
	formatParameterLabel,
	orderRelationships,
} from "./format";
import { SchemaSection } from "./SchemaSection";
import { ViewsSection } from "./ViewsSection";
import { emptyView, withEntities } from "./viewEditing";
import { removeViewXml, replaceViewBlock, replaceViewXml, viewNameFromXml, viewToXml } from "./viewDocument";

const OUTPUT_OPTIONS = [
	{ value: "split", label: "Table + JSON" },
	{ value: "table", label: "Table only" },
	{ value: "json", label: "JSON only" },
];

const EXPORT_OPTIONS = [
	{ format: "json", label: "JSON" },
	{ format: "csv", label: "CSV" },
	{ format: "xlsx", label: "Excel" },
	{ format: "native", label: "Native" },
];

const MEASURE_OPTIONS = [
	{ value: "count", label: "Count" },
	{ value: "sum", label: "Sum" },
	{ value: "avg", label: "Average" },
	{ value: "min", label: "Min" },
	{ value: "max", label: "Max" },
];

const PAGE_SIZE_OPTIONS = [
	{ value: 5, label: "5" },
	{ value: 10, label: "10" },
	{ value: 20, label: "20" },
	{ value: 50, label: "50" },
	{ value: 0, label: "All" },
];

function projectionFields(view) {
	if (!view?.projection) {
		return [];
	}

	return Object.entries(view.projection).flatMap(([entity, attributes]) =>
		(attributes ?? []).map((attribute) => ({
			entity,
			attribute,
			key: `${entity}.${attribute}`,
			label: `${formatEntityName(entity)} · ${formatParameterLabel(attribute)}`,
		})),
	);
}

function pageLabel(result) {
	if (!result) {
		return "No run yet";
	}

	const total = result.totalCount ?? result.rowCount;
	if (!result.rowCount) {
		return `0 of ${total}`;
	}

	const start = (result.offset ?? 0) + 1;
	const end = (result.offset ?? 0) + result.rowCount;
	return `${start}–${end} of ${total}`;
}

function sqlValue(raw) {
	const text = String(raw ?? "").trim();
	if (!text) {
		return "NULL";
	}
	if (/^-?\d+(\.\d+)?$/.test(text)) {
		return text;
	}
	return `'${text.replaceAll("'", "''")}'`;
}

function bindParameterText(text, parameters, values) {
	if (!text) {
		return text ?? "";
	}
	return String(text).replaceAll(/\$([A-Za-z_][A-Za-z0-9_]*)/g, (match, name) => {
		const spec = (parameters ?? []).find((item) => item.name === name);
		const raw = values?.[name];
		if (raw == null || String(raw).trim() === "") {
			return spec?.default ?? match;
		}
		return String(raw);
	});
}

function sortLabel(sortKey) {
	if (!sortKey) {
		return "";
	}
	const bare = sortKey.startsWith("alias:") ? sortKey.slice("alias:".length) : sortKey.split(".").at(-1);
	return formatParameterLabel(bare);
}

function querySummary({
	view,
	parameterValues,
	joinTypes,
	groupBy,
	measures,
	having,
	sortKey,
	sortDirection,
	pageSize,
}) {
	if (!view) {
		return "Choose a view to see what will run.";
	}

	const entities = (view.baseEntities ?? []).map(formatEntityName).join(", ");
	const joins = (view.relationshipRefs ?? []).map((ref, index) => {
		const type = (joinTypes[index] || ref.type || "INNER").toLowerCase();
		return `${type} join ${ref.name}`;
	});
	const filters = (view.filters ?? []).map((filter) => {
		const text = bindParameterText(formatFilterCondition(filter), view.parameters, parameterValues);
		return `${formatEntityName(filter.entity)} where ${text}`;
	});
	const parameters = (view.parameters ?? [])
		.filter((param) => parameterValues?.[param.name] != null && String(parameterValues[param.name]).trim() !== "")
		.map((param) => `${formatParameterLabel(param.name)} = ${parameterValues[param.name]}`);
	const sentences = [`Run ${view.name} across ${entities || "the selected entities"}.`];
	if (parameters.length) {
		sentences.push(`Parameters: ${parameters.join(", ")}.`);
	}
	if (joins.length) {
		sentences.push(`Join with ${joins.join(", then ")}.`);
	}
	if (filters.length) {
		sentences.push(`Keep rows for ${filters.join("; ")}.`);
	}
	if (groupBy.length) {
		const groups = groupBy.map((item) => formatParameterLabel(item.attribute)).join(", ");
		const measureText = (measures.length ? measures : [{ fn: "count", attribute: "" }])
			.map((measure) => `${measure.fn} of ${formatParameterLabel(measure.attribute || "rows")} as ${formatParameterLabel(measure.alias)}`)
			.join(", ");
		sentences.push(`Group by ${groups} and calculate ${measureText}.`);
		const activeHaving = having.filter((item) => String(item.value ?? "").trim() !== "");
		if (activeHaving.length) {
			sentences.push(
				`Keep groups where ${activeHaving
					.map((item) => `${formatParameterLabel(item.alias)} ${item.operator} ${bindParameterText(item.value, view.parameters, parameterValues)}`)
					.join(" and ")}.`,
			);
		}
	}
	if (sortKey) {
		sentences.push(`Sort by ${sortLabel(sortKey)} ${sortDirection}.`);
	}
	sentences.push(pageSize ? `Show ${pageSize} rows per page.` : "Show every matching row.");
	return sentences.join(" ");
}

function buildSqlPreview({
	view,
	summary,
	parameterValues,
	joinTypes,
	groupBy,
	measures,
	having,
	sortKey,
	sortDirection,
	pageSize,
}) {
	if (!view) {
		return { logicalSql: "", sourceQueries: [] };
	}

	const entities = Object.fromEntries((summary?.entities ?? []).map((entity) => [entity.name, entity]));
	const databases = Object.fromEntries((summary?.databases ?? []).map((database) => [database.id, database]));
	const relationships = Object.fromEntries((summary?.relationships ?? []).map((relationship) => [relationship.name, relationship]));
	const parameters = view.parameters ?? [];
	const where = [];

	for (const filter of view.filters ?? []) {
		const raw = filter.query
			? bindParameterText(formatFilterCondition(filter), parameters, parameterValues)
			: `${filter.attribute} ${filter.operator} ${bindParameterText(filter.value, parameters, parameterValues)}`;
		const match = raw.match(/^(.+?)\s*(>=|<=|!=|=|>|<)\s*(.+)$/);
		if (!match) {
			continue;
		}
		const column = match[1].trim().split("/").at(-1);
		where.push(`${filter.entity}.${column} ${match[2]} ${sqlValue(match[3])}`);
	}

	const select = [];
	if (groupBy.length) {
		select.push(...groupBy.map((item) => `${item.entity}.${item.attribute}`));
		for (const measure of measures.length ? measures : [{ fn: "count", alias: "row_count" }]) {
			const argument = measure.attribute ? `${measure.entity}.${measure.attribute}` : "*";
			select.push(`${measure.fn.toUpperCase()}(${argument}) AS ${measure.alias}`);
		}
	} else {
		for (const [entity, attributes] of Object.entries(view.projection ?? {})) {
			for (const attribute of attributes ?? []) {
				select.push(`${entity}.${attribute}`);
			}
		}
	}

	const joinWords = {
		INNER: "INNER JOIN",
		LEFT: "LEFT JOIN",
		RIGHT: "RIGHT JOIN",
		FULL: "FULL OUTER JOIN",
		SEMI: "SEMI JOIN",
		ANTI: "ANTI JOIN",
	};
	const baseEntities = view.baseEntities ?? [];
	const joined = new Set(baseEntities[0] ? [baseEntities[0]] : []);
	const joinLines = [];
	(view.relationshipRefs ?? []).forEach((ref, index) => {
		const relationship = relationships[ref.name];
		if (!relationship?.condition) {
			return;
		}
		const type = joinTypes[index] || ref.type || "INNER";
		const nextEntity = joined.has(relationship.left_entity) ? relationship.right_entity : relationship.left_entity;
		const condition = relationship.condition;
		joinLines.push(
			`${joinWords[type] || "INNER JOIN"} ${nextEntity}\n  ON ${condition.left_entity}.${condition.left_attribute} ${condition.operator || "="} ${condition.right_entity}.${condition.right_attribute}`,
		);
		joined.add(nextEntity);
	});

	const lines = [
		"SELECT",
		`  ${(select.length ? select : ["*"]).join(",\n  ")}`,
		`FROM ${baseEntities[0] || ""}`,
		...joinLines,
	];
	if (where.length) {
		lines.push(`WHERE ${where.join("\n  AND ")}`);
	}
	if (groupBy.length) {
		lines.push(`GROUP BY ${groupBy.map((item) => `${item.entity}.${item.attribute}`).join(", ")}`);
		const activeHaving = having.filter((item) => String(item.value ?? "").trim() !== "");
		if (activeHaving.length) {
			lines.push(
				`HAVING ${activeHaving
					.map((item) => `${item.alias} ${item.operator} ${sqlValue(bindParameterText(item.value, parameters, parameterValues))}`)
					.join("\n   AND ")}`,
			);
		}
	}
	if (sortKey) {
		const column = sortKey.startsWith("alias:")
			? sortKey.slice("alias:".length)
			: sortKey;
		lines.push(`ORDER BY ${column} ${sortDirection.toUpperCase()}`);
	}
	if (pageSize) {
		lines.push(`LIMIT ${pageSize}`);
	}

	const sourceQueries = baseEntities.map((entityName) => {
		const entity = entities[entityName];
		const database = databases[entity?.databaseRef];
		const attributes = view.projection?.[entityName] ?? entity?.attributes ?? ["*"];
		const entityFilters = (view.filters ?? []).filter((filter) => filter.entity === entityName);
		if (database?.type === "XML") {
			const xmlFilter = entityFilters.find((filter) => filter.query);
			const text = xmlFilter
				? bindParameterText(xmlFilter.query, parameters, parameterValues)
				: (entity?.basePath || `/${entityName}`);
			return { entity: entityName, language: "XPath", text };
		}
		const entityWhere = where
			.filter((item) => item.startsWith(`${entityName}.`))
			.map((item) => item.slice(entityName.length + 1));
		const statement = [`SELECT ${attributes.join(", ")}`, `FROM ${entityName}`];
		if (entityWhere.length) {
			statement.push(`WHERE ${entityWhere.join(" AND ")}`);
		}
		return { entity: entityName, language: "SQL", text: statement.join("\n") };
	});

	return { logicalSql: `${lines.join("\n")};`, sourceQueries };
}

const HAVING_OPERATORS = [">", "<", ">=", "<=", "=", "!="];

function nextAlias(base, used) {
	if (!used.includes(base)) {
		return base;
	}

	let index = 2;
	while (used.includes(`${base}_${index}`)) {
		index += 1;
	}
	return `${base}_${index}`;
}

function aliasForMeasure(fn, attribute, used) {
	const base = fn === "count" && !attribute ? "row_count" : `${fn}_${attribute || "rows"}`;
	return nextAlias(base, used);
}

function formatValue(value) {
	if (value === null || value === undefined) {
		return "—";
	}

	if (typeof value === "object") {
		return JSON.stringify(value);
	}

	return String(value);
}

function responseForDisplay(result) {
	if (!result) {
		return null;
	}

	return {
		viewName: result.viewName,
		rowCount: result.rowCount,
		totalCount: result.totalCount,
		limit: result.limit,
		offset: result.offset,
		elapsedMs: result.elapsedMs,
		parameters: result.parameters,
		joins: result.joins,
		columns: result.columns,
		rows: result.rows,
	};
}

function filenameFromDisposition(header, fallback) {
	const match = header?.match(/filename="([^"]+)"/);
	return match?.[1] || fallback;
}

function downloadBlob(filename, blob) {
	const url = URL.createObjectURL(blob);
	const link = document.createElement("a");
	link.href = url;
	link.download = filename;
	document.body.appendChild(link);
	link.click();
	link.remove();
	URL.revokeObjectURL(url);
}

function formatApiError(payload) {
	if (!payload) {
		return "Something went wrong.";
	}

	if (payload.details) {
		return `${payload.error}\n\n${payload.details}`;
	}

	return payload.error || "Something went wrong.";
}

function Metric({ label, value }) {
	return (
		<div className="metric">
			<span>{label}</span>
			<strong>{value}</strong>
		</div>
	);
}

function DetailGroups({ groups, emptyText }) {
	if (!groups.length) {
		return <p className="muted-text">{emptyText}</p>;
	}

	return (
		<div className="detail-stack">
			{groups.map((group) => (
				<div className="detail-block" key={group.title}>
					<strong>{group.title}</strong>
					{group.items ? (
						<InfoList items={group.items} emptyText="" />
					) : (
						<span>{group.text}</span>
					)}
				</div>
			))}
		</div>
	);
}

function InfoList({ items, emptyText }) {
	if (!items.length) {
		return <p className="muted-text">{emptyText}</p>;
	}

	return (
		<div className="info-list">
			{items.map((item) => (
				<code key={item}>{item}</code>
			))}
		</div>
	);
}

function ResultsTable({ columns, rows }) {
	if (!rows.length) {
		return <div className="empty-box">This view returned no rows.</div>;
	}

	const numericColumns = new Set(
		columns.filter((column) =>
			rows.some((row) => typeof row[column] === "number" || (
				typeof row[column] === "string" &&
				row[column].trim() !== "" &&
				Number.isFinite(Number(row[column]))
			)),
		),
	);

	return (
		<div className="table-wrap">
			<table>
				<thead>
					<tr>
						{columns.map((column) => (
							<th className={numericColumns.has(column) ? "numeric-cell" : ""} key={column}>
								{formatParameterLabel(column.replace(".", "_"))}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{rows.map((row, rowIndex) => (
						<tr key={`${rowIndex}-${JSON.stringify(row)}`}>
							{columns.map((column) => (
								<td
									className={numericColumns.has(column) ? "numeric-cell" : ""}
									key={`${rowIndex}-${column}`}
								>
									{formatValue(row[column])}
								</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

function App() {
	const [metaschemaXml, setMetaschemaXml] = useState("");
	const [viewsXml, setViewsXml] = useState("");
	const [summary, setSummary] = useState(null);
	const [selectedView, setSelectedView] = useState("");
	const [outputMode, setOutputMode] = useState("split");
	const [result, setResult] = useState(null);
	const [dataSources, setDataSources] = useState(null);
	const [error, setError] = useState("");
	const [bootstrapping, setBootstrapping] = useState(true);
	const [parsing, setParsing] = useState(false);
	const [running, setRunning] = useState(false);
	const [exporting, setExporting] = useState("");
	const [parameterValues, setParameterValues] = useState({});
	const [joinTypes, setJoinTypes] = useState([]);
	const [groupBy, setGroupBy] = useState([]);
	const [measures, setMeasures] = useState([]);
	const [having, setHaving] = useState([]);
	const [sortKey, setSortKey] = useState("");
	const [sortDirection, setSortDirection] = useState("asc");
	const [pageSize, setPageSize] = useState(10);
	const [page, setPage] = useState(1);
	const [lastQuery, setLastQuery] = useState(null);
	const [setupTab, setSetupTab] = useState("query");
	const [savedViewsXml, setSavedViewsXml] = useState("");
	const [saving, setSaving] = useState(false);
	const [materializing, setMaterializing] = useState("");
	const [savingSchema, setSavingSchema] = useState(false);

	useEffect(() => {
		void loadBootstrap();
	}, []);

	const activeView = useMemo(() => {
		return summary?.views.find((view) => view.name === selectedView) ?? null;
	}, [selectedView, summary]);
	const entityLink = useMemo(
		() => orderRelationships(activeView?.baseEntities ?? [], summary?.relationships),
		[activeView, summary],
	);

	const parameterSignature = (activeView?.parameters ?? [])
		.map((param) => `${param.name}:${param.type}:${param.default ?? ""}`)
		.join("|");

	const joinSignature = (activeView?.relationshipRefs ?? [])
		.map((ref) => `${ref.name}:${ref.type}`)
		.join("|");

	useEffect(() => {
		const next = {};
		for (const param of activeView?.parameters ?? []) {
			next[param.name] = param.default ?? "";
		}
		setParameterValues(next);
	}, [selectedView, parameterSignature]);

	const shapeSignature = [
		...(activeView?.groupBy ?? []).map((item) => `${item.entity}.${item.attribute}`),
		...(activeView?.measures ?? []).map((item) => `${item.fn}:${item.alias}:${item.entity ?? ""}.${item.attribute ?? ""}`),
		...(activeView?.having ?? []).map((item) => `${item.alias}:${item.operator}:${item.value ?? ""}`),
		...(activeView?.orderBy ?? []).map((item) => `${item.alias ?? ""}.${item.entity ?? ""}.${item.attribute ?? ""}:${item.direction}`),
		activeView?.limit ?? "",
	].join("|");

	useEffect(() => {
		setJoinTypes((activeView?.relationshipRefs ?? []).map((ref) => ref.type || "INNER"));
	}, [selectedView, joinSignature]);

	useEffect(() => {
		const firstSort = activeView?.orderBy?.[0];
		setGroupBy(activeView?.groupBy ?? []);
		setMeasures(activeView?.measures ?? []);
		setHaving(
			(activeView?.having ?? []).map((item) => ({
				alias: item.alias,
				operator: item.operator || ">",
				value: item.value ?? "",
			})),
		);
		if (firstSort?.alias) {
			setSortKey(`alias:${firstSort.alias}`);
		} else if (firstSort?.attribute) {
			setSortKey(`${firstSort.entity}.${firstSort.attribute}`);
		} else {
			setSortKey("");
		}
		setSortDirection(firstSort?.direction || "asc");
		setPageSize(activeView?.limit ?? 10);
		setPage(1);
	}, [selectedView, shapeSignature]);

	async function loadBootstrap() {
		if (viewsXml && viewsXml !== savedViewsXml && !window.confirm("Discard view changes that are not saved to views.xml?")) {
			return;
		}
		setBootstrapping(true);
		setError("");

		try {
			const response = await fetch("/api/bootstrap");
			const payload = await response.json();

			if (!response.ok) {
				throw new Error(formatApiError(payload));
			}

			setMetaschemaXml(payload.metaschemaXml);
			setViewsXml(payload.viewsXml);
			setSavedViewsXml(payload.viewsXml);
			setSummary(payload.summary);
			setDataSources(payload.dataSources);
			setSelectedView(payload.summary.viewNames[0] ?? "");
			setResult(null);
		} catch (err) {
			setError(err.message);
		} finally {
			setBootstrapping(false);
		}
	}

	async function inspectDefinitions(nextViewsXml = viewsXml, preferredView = "") {
		setParsing(true);
		setError("");

		try {
			const response = await fetch("/api/inspect", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({
					metaschemaXml,
					viewsXml: nextViewsXml,
				}),
			});
			const payload = await response.json();

			if (!response.ok) {
				throw new Error(formatApiError(payload));
			}

			setViewsXml(nextViewsXml);
			setSummary(payload);
			setSelectedView((current) => {
				if (preferredView && payload.viewNames.includes(preferredView)) {
					return preferredView;
				}
				return payload.viewNames.includes(current) ? current : (payload.viewNames[0] ?? "");
			});
			setResult(null);
			return "";
		} catch (err) {
			return err.message;
		} finally {
			setParsing(false);
		}
	}

	async function saveViewsFile() {
		setSaving(true);
		setError("");

		try {
			const response = await fetch("/api/save-views", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({ metaschemaXml, viewsXml }),
			});
			const payload = await response.json();

			if (!response.ok) {
				throw new Error(formatApiError(payload));
			}

			setSummary(payload);
			setSavedViewsXml(viewsXml);
		} catch (err) {
			setError(err.message);
		} finally {
			setSaving(false);
		}
	}

	async function saveMetaschema(nextMetaschemaXml) {
		setSavingSchema(true);
		setError("");
		try {
			const response = await fetch("/api/save-metaschema", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({ metaschemaXml: nextMetaschemaXml, viewsXml }),
			});
			const payload = await response.json();
			if (!response.ok) {
				throw new Error(formatApiError(payload));
			}
			setMetaschemaXml(nextMetaschemaXml);
			setSummary(payload);
			return "";
		} catch (err) {
			return err.message;
		} finally {
			setSavingSchema(false);
		}
	}

	async function testConnection(databaseId) {
		try {
			const response = await fetch("/api/test-connection", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({ metaschemaXml, databaseId }),
			});
			const payload = await response.json();
			if (!response.ok) {
				return { ok: false, message: formatApiError(payload) };
			}
			return payload;
		} catch (err) {
			return { ok: false, message: err.message };
		}
	}

	async function postMaterialize(path, body) {
		const response = await fetch(path, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
			},
			body: JSON.stringify({ metaschemaXml, viewsXml, ...body }),
		});
		const payload = await response.json();
		if (!response.ok) {
			throw new Error(formatApiError(payload));
		}
		setMetaschemaXml(payload.metaschemaXml);
		setSummary(payload.summary);
	}

	async function materializeView(viewName) {
		setMaterializing(viewName);
		setError("");
		try {
			await postMaterialize("/api/materialize", {
				viewName,
				parameters: viewName === selectedView ? parameterValues : {},
			});
		} catch (err) {
			setError(err.message);
		} finally {
			setMaterializing("");
		}
	}

	async function dropMaterialized(entityName) {
		if (!window.confirm(`Remove ${formatEntityName(entityName)} and its stored rows?`)) {
			return false;
		}
		setMaterializing(entityName);
		setError("");
		try {
			await postMaterialize("/api/materialize/drop", { entity: entityName });
			return true;
		} catch (err) {
			window.alert(err.message);
			return false;
		} finally {
			setMaterializing("");
		}
	}

	function buildOrderBy() {
		if (!sortKey) {
			return [];
		}

		if (sortKey.startsWith("alias:")) {
			return [{ alias: sortKey.slice("alias:".length), direction: sortDirection }];
		}

		const field = projectionFields(activeView).find((item) => item.key === sortKey);
		if (!field) {
			return [];
		}

		return [{ entity: field.entity, attribute: field.attribute, direction: sortDirection }];
	}

	async function runQuery(nextPage = page) {
		const requestedPage = typeof nextPage === "number" ? nextPage : page;
		if (!selectedView) {
			setError("Pick a logical view before running the query.");
			return;
		}
		if (!activeView?.baseEntities?.length) {
			setError("Choose at least one entity.");
			return;
		}
		if (entityLink.missing.length) {
			setError(`No relationship connects ${entityLink.missing.map(formatEntityName).join(", ")}.`);
			return;
		}

		const requestBody = {
			metaschemaXml,
			viewsXml,
			viewName: selectedView,
			parameters: parameterValues,
			joinTypes: (activeView?.relationshipRefs ?? []).map(
				(ref, index) => joinTypes[index] || ref.type || "INNER",
			),
			groupBy,
			measures: groupBy.length ? measures : [],
			having: groupBy.length
				? having.filter((item) => item.alias && String(item.value ?? "").trim() !== "")
				: [],
			orderBy: buildOrderBy(),
			limit: pageSize || null,
			offset: pageSize ? (requestedPage - 1) * pageSize : 0,
		};

		setRunning(true);
		setError("");

		try {
			const response = await fetch("/api/execute", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify(requestBody),
			});
			const payload = await response.json();

			if (!response.ok) {
				throw new Error(formatApiError(payload));
			}

			setResult(payload);
			setLastQuery(requestBody);
		} catch (err) {
			setError(err.message);
		} finally {
			setRunning(false);
		}
	}

	function forgetAlias(alias) {
		setHaving((current) => current.filter((item) => item.alias !== alias));
		setSortKey((current) => (current === `alias:${alias}` ? "" : current));
	}

	function updateMeasure(index, patch) {
		setPage(1);
		setMeasures((current) => current.map((measure, itemIndex) => (
			itemIndex === index ? { ...measure, ...patch } : measure
		)));
	}

	async function downloadExport(format) {
		if (!lastQuery) {
			return;
		}

		setExporting(format);
		setError("");

		try {
			const response = await fetch("/api/execute", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({
					...lastQuery,
					format,
					limit: null,
					offset: 0,
				}),
			});

			if (!response.ok) {
				const payload = await response.json();
				throw new Error(formatApiError(payload));
			}

			const blob = await response.blob();
			const filename = filenameFromDisposition(
				response.headers.get("Content-Disposition"),
				`${lastQuery.viewName}.${format === "xlsx" ? "xlsx" : format === "csv" ? "csv" : "json"}`,
			);
			downloadBlob(filename, blob);
		} catch (err) {
			setError(err.message);
		} finally {
			setExporting("");
		}
	}

	const showTable = outputMode === "split" || outputMode === "table";
	const showJson = outputMode === "split" || outputMode === "json";
	const selectionSummary = querySummary({
		view: activeView,
		parameterValues,
		joinTypes,
		groupBy,
		measures,
		having,
		sortKey,
		sortDirection,
		pageSize,
	});
	const structureBlocked = !activeView?.baseEntities?.length || entityLink.missing.length > 0;
	const stepShapeText = [
		groupBy.length ? `group by ${groupBy.map((item) => item.attribute).join(", ")}` : "no grouping",
		sortKey ? `sort by ${sortLabel(sortKey)} ${sortDirection}` : "unsorted",
		pageSize ? `${pageSize} rows per page` : "all rows",
	].join(", ");
	const stepShape = {
		groupBy,
		measures: groupBy.length ? measures : [],
		having: groupBy.length ? having.filter((item) => item.alias && String(item.value ?? "").trim() !== "") : [],
		orderBy: buildOrderBy(),
		limit: pageSize || null,
		offset: 0,
	};

	function saveView(nextView, previousName = nextView.name) {
		setSummary((current) => {
			if (!current) {
				return current;
			}
			const exists = current.views.some((view) => view.name === previousName);
			const views = exists
				? current.views.map((view) => (view.name === previousName ? nextView : view))
				: [...current.views, nextView];
			const viewNames = [
				...current.viewNames.filter((name) => name !== previousName),
				nextView.name,
			].sort((left, right) => left.localeCompare(right));
			return {
				...current,
				views,
				viewNames,
				counts: { ...current.counts, views: viewNames.length },
			};
		});
		setViewsXml((current) => replaceViewXml(current, nextView, previousName));
		if (nextView.name !== previousName) {
			setSelectedView(nextView.name);
		}
		setResult(null);
		setPage(1);
	}

	function renameView(rawName) {
		const name = rawName.trim();
		if (!activeView) {
			return "";
		}
		if (!name) {
			return "Enter a name for the view.";
		}
		if (name !== activeView.name && summary?.viewNames.includes(name)) {
			return "Another view already uses this name.";
		}
		if (name !== activeView.name) {
			saveView({ ...activeView, name }, activeView.name);
		}
		return "";
	}

	function deleteView() {
		if (!activeView || !window.confirm(`Delete "${activeView.name}"? Save to views.xml to make it permanent.`)) {
			return;
		}
		const name = activeView.name;
		const remaining = (summary?.viewNames ?? []).filter((item) => item !== name);
		setSummary((current) => current && {
			...current,
			views: current.views.filter((view) => view.name !== name),
			viewNames: remaining,
			counts: { ...current.counts, views: remaining.length },
		});
		setViewsXml((current) => removeViewXml(current, name));
		setSelectedView(remaining[0] ?? "");
		setResult(null);
	}

	function applyViewXml(block) {
		const nextViewsXml = replaceViewBlock(viewsXml, activeView?.name ?? "", block);
		return inspectDefinitions(nextViewsXml, viewNameFromXml(block));
	}

	function createView(rawName, entityNames) {
		const name = rawName.trim();
		if (!name) {
			return "Enter a name for the view.";
		}
		if (summary?.viewNames.includes(name)) {
			return "Another view already uses this name.";
		}
		saveView(withEntities(emptyView(name), entityNames, summary?.entities, summary?.relationships));
		setSelectedView(name);
		return "";
	}

	const queryPlan = buildSqlPreview({
		view: activeView,
		summary,
		parameterValues,
		joinTypes,
		groupBy,
		measures,
		having,
		sortKey,
		sortDirection,
		pageSize,
	});

	return (
		<div className="app-shell">
			<div className="page">
				<header className="hero">
					<div className="hero-copy">
						<p className="label">XDM Views</p>
						<h1>Federated views across relational and XML data.</h1>
						<p className="hero-text">
							See the entities in the schema, build or change a view from them, then run it across the relational and XML sources.
						</p>
						<p className="byline">
							Made by Devanshi Bavaria (MT2025041) and Parag Katoch (MT2025082)
						</p>
					</div>

					<div className="metrics">
						<Metric label="Databases" value={summary?.counts.databases ?? "—"} />
						<Metric label="Entities" value={summary?.counts.entities ?? "—"} />
						<Metric label="Views" value={summary?.counts.views ?? "—"} />
						<Metric label="Relationships" value={summary?.counts.relationships ?? "—"} />
					</div>
				</header>

				<nav className="workflow-strip" aria-label="Project workflow">
					{[
						["entities", "1", "Entities"],
						["views", "2", "Views"],
						["select", "3", "Parameters and shape"],
						["summary", "4", "Summary and SQL"],
						["results", "5", "Results + export"],
					].map(([id, number, label]) => (
						<button
							key={id}
							type="button"
							className="workflow-step"
							onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })}
						>
							<strong>{number}</strong>
							{label}
						</button>
					))}
				</nav>

				<main className="layout">
					<SchemaSection
						summary={summary}
						metaschemaXml={metaschemaXml}
						saving={savingSchema}
						onReload={loadBootstrap}
						onSaveMetaschema={saveMetaschema}
						onTestConnection={testConnection}
						materializing={materializing}
						onRefreshMaterialized={materializeView}
						onDropMaterialized={dropMaterialized}
					/>

					<ViewsSection
						summary={summary}
						activeView={activeView}
						viewXml={activeView ? viewToXml(activeView) : ""}
						viewsXml={viewsXml}
						dirty={viewsXml !== savedViewsXml}
						saving={saving}
						applying={parsing}
						stepShape={stepShape}
						stepShapeText={stepShapeText}
						onSelect={(name) => {
							setSelectedView(name);
							setResult(null);
							setPage(1);
						}}
						onCreate={createView}
						onRename={renameView}
						onDelete={deleteView}
						onChange={(view) => saveView(view)}
						onApplyViewXml={applyViewXml}
						onApplyAllXml={(xml) => inspectDefinitions(xml)}
						onSaveFile={saveViewsFile}
						materializing={materializing}
						materializeBlocked={
							structureBlocked
								? "Add entities and make sure they are connected by joins before storing the result."
								: ""
						}
						onMaterialize={() => materializeView(activeView.name)}
					/>

					<section className="panel" id="select">
						<div className="section-header section-header--compact">
							<div>
								<p className="label">Step 3 · Parameters and shape</p>
								<h2>Set the values for this run</h2>
								<p className="section-description">
									Parameter values apply to this run. Grouping, sort, and page size do too.
								</p>
							</div>
						</div>

						<div className="setup-tabs" role="tablist" aria-label="Query configuration">
							<button
								type="button"
								role="tab"
								aria-selected={setupTab === "query"}
								className={setupTab === "query" ? "setup-tab setup-tab--active" : "setup-tab"}
								onClick={() => setSetupTab("query")}
							>
								Query setup
							</button>
							<button
								type="button"
								role="tab"
								aria-selected={setupTab === "transform"}
								className={setupTab === "transform" ? "setup-tab setup-tab--active" : "setup-tab"}
								onClick={() => setSetupTab("transform")}
							>
								Transform result
							</button>
						</div>

						{setupTab === "query" ? (
						<div className="runner-section">
							<p className="section-description">Fill in the parameters saved on this view.</p>
						<div className="controls-grid controls-grid--single">
							<label className="field">
								<span>Output</span>
								<select value={outputMode} onChange={(event) => setOutputMode(event.target.value)}>
									{OUTPUT_OPTIONS.map((option) => (
										<option key={option.value} value={option.value}>
											{option.label}
										</option>
									))}
								</select>
							</label>
						</div>

						{(activeView?.parameters ?? []).length ? (
							<div className="parameter-panel">
								<p className="label">Parameters</p>
								<div className="parameter-grid">
									{activeView.parameters.map((param) => (
										<label className="field" key={param.name}>
											<span className="field-label">{formatParameterLabel(param.name)}</span>
											<span className="field-hint">{formatParameterHint(param)}</span>
											<input
												type={param.type === "string" ? "text" : "number"}
												step={param.type === "float" ? "any" : "1"}
												value={parameterValues[param.name] ?? ""}
												onChange={(event) =>
													setParameterValues((current) => ({
														...current,
														[param.name]: event.target.value,
													}))
												}
											/>
										</label>
									))}
								</div>
							</div>
						) : null}

						</div>
						) : (
						<div className="runner-section">
							<p className="section-description">Group, calculate, filter aggregated rows, sort, and choose the page size.</p>
						<div className="parameter-panel">
							<p className="label">Group, sort, and page</p>
							<div className="parameter-grid">
								<label className="field">
									<span className="field-label">Group by</span>
									<select
										value={groupBy[0] ? `${groupBy[0].entity}.${groupBy[0].attribute}` : ""}
										onChange={(event) => {
											const key = event.target.value;
											setPage(1);
											if (!key) {
												setGroupBy([]);
												setMeasures([]);
												setHaving([]);
												setSortKey((current) => (current.startsWith("alias:") ? "" : current));
												return;
											}
											const field = projectionFields(activeView).find((item) => item.key === key);
											if (!field) {
												return;
											}
											setSortKey((current) => {
												if (!current || current.startsWith("alias:") || current === key) {
													return current;
												}
												return "";
											});
											setGroupBy([{ entity: field.entity, attribute: field.attribute }]);
											setMeasures((current) =>
												current.length ? current : [{ fn: "count", alias: "row_count" }],
											);
										}}
									>
										<option value="">No grouping</option>
										{projectionFields(activeView).map((field) => (
											<option key={field.key} value={field.key}>
												{field.label}
											</option>
										))}
									</select>
								</label>

								<label className="field">
									<span className="field-label">Sort by</span>
									<select
										value={sortKey}
										onChange={(event) => {
											setSortKey(event.target.value);
											setPage(1);
										}}
									>
										<option value="">Unsorted</option>
										{(groupBy.length
											? projectionFields(activeView).filter((field) =>
													groupBy.some(
														(item) =>
															item.entity === field.entity && item.attribute === field.attribute,
													),
												)
											: projectionFields(activeView)
										).map((field) => (
											<option key={field.key} value={field.key}>
												{field.label}
											</option>
										))}
										{measures.map((measure) => (
											<option key={`alias:${measure.alias}`} value={`alias:${measure.alias}`}>
												{formatParameterLabel(measure.alias)}
											</option>
										))}
									</select>
								</label>

								<label className="field">
									<span className="field-label">Direction</span>
									<select
										value={sortDirection}
										disabled={!sortKey}
										onChange={(event) => {
											setSortDirection(event.target.value);
											setPage(1);
										}}
									>
										<option value="asc">Ascending</option>
										<option value="desc">Descending</option>
									</select>
								</label>

								<label className="field">
									<span className="field-label">Rows per page</span>
									<select
										value={String(pageSize)}
										onChange={(event) => {
											setPageSize(Number(event.target.value));
											setPage(1);
										}}
									>
										{PAGE_SIZE_OPTIONS.map((option) => (
											<option key={option.value} value={option.value}>
												{option.label}
											</option>
										))}
									</select>
								</label>
							</div>

							<div className="shape-section">
								<p className="label">Measures</p>
								{groupBy.length ? measures.map((measure, index) => (
									<div className="shape-row" key={`${measure.alias}-${index}`}>
										<label className="field">
											<span className="field-label">Function</span>
											<select
												value={measure.fn}
												onChange={(event) => {
													const fn = event.target.value;
													if (fn !== "count" && !measure.attribute) {
														const field = projectionFields(activeView)[0];
														updateMeasure(index, field
															? { fn, entity: field.entity, attribute: field.attribute }
															: { fn });
														return;
													}
													updateMeasure(index, { fn });
												}}
											>
												{MEASURE_OPTIONS.map((option) => (
													<option key={option.value} value={option.value}>
														{option.label}
													</option>
												))}
											</select>
										</label>
										<label className="field">
											<span className="field-label">Field</span>
											<select
												value={measure.attribute ? `${measure.entity}.${measure.attribute}` : ""}
												onChange={(event) => {
													const key = event.target.value;
													if (!key) {
														updateMeasure(index, { entity: undefined, attribute: undefined });
														return;
													}
													const field = projectionFields(activeView).find((item) => item.key === key);
													if (!field) {
														return;
													}
													updateMeasure(index, {
														entity: field.entity,
														attribute: field.attribute,
													});
												}}
											>
												<option value="">All rows</option>
												{projectionFields(activeView).map((field) => (
													<option key={field.key} value={field.key}>
														{field.label}
													</option>
												))}
											</select>
										</label>
										<span className="field-hint">as {formatParameterLabel(measure.alias)}</span>
										<button
											type="button"
											className="button button--secondary button--small"
											onClick={() => {
												setPage(1);
												setMeasures((current) => current.filter((_, itemIndex) => itemIndex !== index));
												forgetAlias(measure.alias);
											}}
											disabled={measures.length < 2}
										>
											Remove
										</button>
									</div>
								)) : (
									<p className="field-hint">No grouping. Sort and page apply to the joined rows.</p>
								)}
								<button
									type="button"
									className="button button--secondary button--small"
									disabled={!groupBy.length}
									onClick={() => {
										setPage(1);
										setMeasures((current) => [
											...current,
											{
												fn: "count",
												alias: aliasForMeasure("count", "", current.map((item) => item.alias)),
											},
										]);
									}}
								>
									Add measure
								</button>
							</div>

							<div className="shape-section">
								<p className="label">Filter aggregated results</p>
								{having.map((condition, index) => (
									<div className="shape-row" key={`${condition.alias}-${index}`}>
										<label className="field">
											<span className="field-label">Measure</span>
											<select
												value={condition.alias}
												onChange={(event) => {
													const alias = event.target.value;
													setHaving((current) => current.map((item, itemIndex) => (
														itemIndex === index ? { ...item, alias } : item
													)));
													setPage(1);
												}}
											>
												{measures.map((measure) => (
													<option key={measure.alias} value={measure.alias}>
														{formatParameterLabel(measure.alias)}
													</option>
												))}
											</select>
										</label>
										<label className="field field--narrow">
											<span className="field-label">Operator</span>
											<select
												value={condition.operator}
												onChange={(event) => {
													const operator = event.target.value;
													setHaving((current) => current.map((item, itemIndex) => (
														itemIndex === index ? { ...item, operator } : item
													)));
													setPage(1);
												}}
											>
												{HAVING_OPERATORS.map((operator) => (
													<option key={operator} value={operator}>
														{operator}
													</option>
												))}
											</select>
										</label>
										<label className="field">
											<span className="field-label">Value</span>
											<input
												type="text"
												value={condition.value}
												placeholder="0 or $min_orders"
												onChange={(event) => {
													const value = event.target.value;
													setHaving((current) => current.map((item, itemIndex) => (
														itemIndex === index ? { ...item, value } : item
													)));
													setPage(1);
												}}
											/>
										</label>
										<button
											type="button"
											className="button button--secondary button--small"
											onClick={() => {
												setPage(1);
												setHaving((current) => current.filter((_, itemIndex) => itemIndex !== index));
											}}
										>
											Remove
										</button>
									</div>
								))}
								<button
									type="button"
									className="button button--secondary button--small"
									disabled={!measures.length}
									onClick={() => {
										const alias = measures[0]?.alias;
										if (!alias) {
											return;
										}
										setPage(1);
										setHaving((current) => [...current, { alias, operator: ">", value: "" }]);
									}}
								>
									Add condition
								</button>
							</div>
						</div>
						</div>
						)}

						<details className="metadata-disclosure">
						<summary className="label metadata-follow">View details</summary>

						<div className="metadata-banner">
							<div>
								<span>Selected view</span>
								<strong>{activeView?.name ?? "No view selected"}</strong>
							</div>
							<div>
								<span>Execution path</span>
								<strong>{(activeView?.baseEntities ?? []).map(formatEntityName).join(" → ") || "—"}</strong>
							</div>
						</div>

						<div className="details-grid">
							<div className="info-card">
								<p className="label">Base entities</p>
								<InfoList
									items={(activeView?.baseEntities ?? []).map(formatEntityName)}
									emptyText="Pick a view to inspect its entities."
								/>
							</div>

							<div className="info-card">
								<p className="label">Projection</p>
								<DetailGroups
									groups={
										activeView
											? Object.entries(activeView.projection).map(([entity, attrs]) => ({
													title: formatEntityName(entity),
													items: attrs.map(formatParameterLabel),
												}))
											: []
									}
									emptyText="No projection details available."
								/>
							</div>
						</div>

						<div className="source-card">
							<div>
								<p className="label">Relational source</p>
								<code>{dataSources?.relationalSource ?? "Loading..."}</code>
							</div>
							<div>
								<p className="label">XML source</p>
								<code>{dataSources?.xmlPath ?? "Loading..."}</code>
							</div>
						</div>
						</details>
					</section>

					<section className="panel" id="summary">
						<div className="section-header">
							<div>
								<p className="label">Step 4 · Summary and SQL</p>
								<h2>Review the query before it runs</h2>
								<p className="section-description">
									This is the selection from step 2, written as a short description and as SQL. XML sources are still read with XPath; the logical SQL shows the same view as one statement.
								</p>
							</div>
							<button className="button" onClick={runQuery} disabled={running || bootstrapping || !selectedView || structureBlocked}>
								{running ? "Running..." : "Run query"}
							</button>
						</div>
						<div className="query-summary">
							<span>What will run</span>
							<p>{selectionSummary}</p>
						</div>
						<div className="sql-panel">
							<p className="label">Logical SQL</p>
							<pre className="sql-block">{queryPlan.logicalSql}</pre>
						</div>
						<div className="source-query-list">
							{queryPlan.sourceQueries.map((query) => (
								<div className="sql-panel" key={query.entity}>
									<p className="label">{query.language} · {query.entity}</p>
									<pre className="sql-block">{query.text}</pre>
								</div>
							))}
						</div>
					</section>

					<section className="panel" id="results">
						<div className="section-header">
							<div>
								<p className="label">Step 5 · Results and export</p>
								<h2>Review the federated output</h2>
							</div>
							<div className="result-summary">
								<span>{pageLabel(result)}</span>
								<span>{result ? `${result.elapsedMs} ms` : "Waiting"}</span>
								{result?.limit ? (
									<>
										<button
											className="button button--secondary button--small"
											onClick={() => {
												const next = Math.max(1, page - 1);
												setPage(next);
												runQuery(next);
											}}
											disabled={running || page <= 1}
										>
											Previous
										</button>
										<button
											className="button button--secondary button--small"
											onClick={() => {
												const next = page + 1;
												setPage(next);
												runQuery(next);
											}}
											disabled={
												running ||
												(result.offset ?? 0) + result.rowCount >= (result.totalCount ?? result.rowCount)
											}
										>
											Next
										</button>
									</>
								) : null}
							</div>
						</div>

						<div className="download-row">
							<span className="label">Download</span>
							{EXPORT_OPTIONS.map((option) => (
								<button
									key={option.format}
									className="button button--secondary button--small"
									onClick={() => downloadExport(option.format)}
									disabled={!lastQuery || Boolean(exporting)}
								>
									{exporting === option.format ? "Preparing..." : option.label}
								</button>
							))}
						</div>

						{error ? <pre className="error-box">{error}</pre> : null}

						{!result && !error ? (
							<div className="empty-box">Run a view to see the joined SQL and XML output here.</div>
						) : null}

						{result ? (
							<div className={`results-grid results-grid--${outputMode}`}>
								{showTable ? (
									<section className="result-card">
										<div className="result-card__header">
											<h3>Table</h3>
											<span>{result.columns.length} columns</span>
										</div>
										<ResultsTable columns={result.columns} rows={result.rows} />
									</section>
								) : null}

								{showJson ? (
									<section className="result-card">
										<div className="result-card__header">
											<h3>JSON</h3>
											<span>Raw response</span>
										</div>
										<pre className="json-block">{JSON.stringify(responseForDisplay(result), null, 2)}</pre>
									</section>
								) : null}
							</div>
						) : null}
					</section>
				</main>
			</div>
		</div>
	);
}

export default App;
