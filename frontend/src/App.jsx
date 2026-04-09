import { useEffect, useMemo, useState } from "react";

const OUTPUT_OPTIONS = [
	{ value: "split", label: "Table + JSON" },
	{ value: "table", label: "Table only" },
	{ value: "json", label: "JSON only" },
];

function formatFilter(filter) {
	if (filter.query) {
		return `${filter.entity}: ${filter.query}`;
	}

	return `${filter.entity}: ${filter.attribute} ${filter.operator} ${filter.value}`;
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

function EditorPanel({ title, hint, value, onChange, onFilePick }) {
	return (
		<section className="editor-card">
			<div className="section-header">
				<div>
					<p className="label">{title}</p>
					<h3>{hint}</h3>
				</div>
				<label className="button button--secondary">
					<input type="file" accept=".xml,text/xml,application/xml" onChange={onFilePick} />
					Upload XML
				</label>
			</div>
			<textarea
				spellCheck="false"
				value={value}
				onChange={(event) => onChange(event.target.value)}
			/>
		</section>
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

	return (
		<div className="table-wrap">
			<table>
				<thead>
					<tr>
						{columns.map((column) => (
							<th key={column}>{column}</th>
						))}
					</tr>
				</thead>
				<tbody>
					{rows.map((row, rowIndex) => (
						<tr key={`${rowIndex}-${JSON.stringify(row)}`}>
							{columns.map((column) => (
								<td key={`${rowIndex}-${column}`}>{formatValue(row[column])}</td>
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

	useEffect(() => {
		void loadBootstrap();
	}, []);

	const activeView = useMemo(() => {
		return summary?.views.find((view) => view.name === selectedView) ?? null;
	}, [selectedView, summary]);

	async function loadBootstrap() {
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

	async function inspectDefinitions() {
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
					viewsXml,
				}),
			});
			const payload = await response.json();

			if (!response.ok) {
				throw new Error(formatApiError(payload));
			}

			setSummary(payload);
			setSelectedView((current) => {
				if (payload.viewNames.includes(current)) {
					return current;
				}

				return payload.viewNames[0] ?? "";
			});
			setResult(null);
		} catch (err) {
			setError(err.message);
		} finally {
			setParsing(false);
		}
	}

	async function runQuery() {
		if (!selectedView) {
			setError("Pick a logical view before running the query.");
			return;
		}

		setRunning(true);
		setError("");

		try {
			const response = await fetch("/api/execute", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({
					metaschemaXml,
					viewsXml,
					viewName: selectedView,
				}),
			});
			const payload = await response.json();

			if (!response.ok) {
				throw new Error(formatApiError(payload));
			}

			setResult(payload);
		} catch (err) {
			setError(err.message);
		} finally {
			setRunning(false);
		}
	}

	async function handleXmlUpload(event, setter) {
		const file = event.target.files?.[0];
		event.target.value = "";

		if (!file) {
			return;
		}

		const text = await file.text();
		setter(text);
	}

	const showTable = outputMode === "split" || outputMode === "table";
	const showJson = outputMode === "split" || outputMode === "json";

	return (
		<div className="app-shell">
			<div className="page">
				<header className="hero">
					<div className="hero-copy">
						<p className="label">XDM Views</p>
						<h1>Simple query workbench for metaschema, views, and output.</h1>
						<p className="hero-text">
							Edit or upload XML, refresh the logical view list, then run the query engine and
							inspect the joined output.
						</p>
					</div>

					<div className="metrics">
						<Metric label="Databases" value={summary?.counts.databases ?? "—"} />
						<Metric label="Entities" value={summary?.counts.entities ?? "—"} />
						<Metric label="Views" value={summary?.counts.views ?? "—"} />
						<Metric label="Relationships" value={summary?.counts.relationships ?? "—"} />
					</div>
				</header>

				<main className="layout">
					<section className="panel">
						<div className="section-header">
							<div>
								<p className="label">Definitions</p>
								<h2>Work with XML safely</h2>
							</div>
							<div className="button-row">
								<button className="button button--secondary" onClick={loadBootstrap}>
									Reload bundled XML
								</button>
								<button className="button" onClick={inspectDefinitions} disabled={parsing}>
									{parsing ? "Refreshing..." : "Refresh view list"}
								</button>
							</div>
						</div>

						<div className="editor-grid">
							<EditorPanel
								title="Metaschema"
								hint="Schema definition"
								value={metaschemaXml}
								onChange={setMetaschemaXml}
								onFilePick={(event) => handleXmlUpload(event, setMetaschemaXml)}
							/>
							<EditorPanel
								title="Views"
								hint="Logical views"
								value={viewsXml}
								onChange={setViewsXml}
								onFilePick={(event) => handleXmlUpload(event, setViewsXml)}
							/>
						</div>
					</section>

					<section className="panel">
						<div className="section-header">
							<div>
								<p className="label">Runner</p>
								<h2>Choose a view and output mode</h2>
							</div>
							<button className="button" onClick={runQuery} disabled={running || bootstrapping}>
								{running ? "Running..." : "Run view"}
							</button>
						</div>

						<div className="controls-grid">
							<label className="field">
								<span>View</span>
								<select
									value={selectedView}
									onChange={(event) => setSelectedView(event.target.value)}
								>
									{summary?.viewNames.length ? null : <option value="">No views available</option>}
									{(summary?.viewNames ?? []).map((viewName) => (
										<option key={viewName} value={viewName}>
											{viewName}
										</option>
									))}
								</select>
							</label>

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

						<div className="runner-grid">
							{/* <div className="info-card">
                <p className="label">Active view</p>
                <h3>{activeView?.name ?? "No view selected"}</h3>
                <p className="muted-text">
                  {bootstrapping
                    ? "Loading bundled definitions..."
                    : "Refresh definitions after editing XML to update this view list."}
                </p>
              </div> */}

							<div className="info-card">
								<p className="label">Available views</p>
								<InfoList
									items={summary?.viewNames ?? []}
									emptyText="No views are currently available."
								/>
							</div>
						</div>

						<div className="details-grid">
							<div className="info-card">
								<p className="label">Base entities</p>
								<InfoList
									items={activeView?.baseEntities ?? []}
									emptyText="Pick a view to inspect its entities."
								/>
							</div>

							<div className="info-card">
								<p className="label">Filters</p>
								<InfoList
									items={(activeView?.filters ?? []).map((filter) => formatFilter(filter))}
									emptyText="No filters defined for this view."
								/>
							</div>

							<div className="info-card">
								<p className="label">Projection</p>
								<InfoList
									items={
										activeView
											? Object.entries(activeView.projection).map(
													([entity, attrs]) => `${entity}: ${attrs.join(", ")}`,
												)
											: []
									}
									emptyText="No projection details available."
								/>
							</div>
						</div>

						<div className="source-card">
							<div>
								<p className="label">SQL source</p>
								<code>{dataSources?.relationalSource ?? "Loading..."}</code>
							</div>
							<div>
								<p className="label">XML source</p>
								<code>{dataSources?.xmlPath ?? "Loading..."}</code>
							</div>
						</div>
					</section>

					<section className="panel">
						<div className="section-header">
							<div>
								<p className="label">Results</p>
								<h2>Query output</h2>
							</div>
							<div className="result-summary">
								<span>{result ? `${result.rowCount} row(s)` : "No run yet"}</span>
								<span>{result ? `${result.elapsedMs} ms` : "Waiting"}</span>
							</div>
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
										<pre className="json-block">{JSON.stringify(result, null, 2)}</pre>
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
