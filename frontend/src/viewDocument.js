const JOIN_TYPES = new Set(["INNER", "LEFT", "RIGHT", "FULL", "SEMI", "ANTI"]);

function xmlAttr(value) {
	return String(value ?? "")
		.replaceAll("&", "&amp;")
		.replaceAll('"', "&quot;")
		.replaceAll("<", "&lt;");
}

function xmlText(value) {
	return String(value ?? "")
		.replaceAll("&", "&amp;")
		.replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;");
}

function escapeRegExp(value) {
	return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function viewToXml(view) {
	const line = (level, text) => `${"  ".repeat(level)}${text}`;
	const lines = [line(1, `<View name="${xmlAttr(view.name)}">`)];

	lines.push(line(2, "<Projection>"));
	for (const [entity, attributes] of Object.entries(view.projection ?? {})) {
		lines.push(line(3, `<Entity name="${xmlAttr(entity)}">`));
		for (const attribute of attributes ?? []) {
			lines.push(line(4, `<Attribute>${xmlText(attribute)}</Attribute>`));
		}
		lines.push(line(3, "</Entity>"));
	}
	lines.push(line(2, "</Projection>"));

	lines.push(line(2, "<BaseEntities>"));
	for (const entity of view.baseEntities ?? []) {
		lines.push(line(3, `<Entity>${xmlText(entity)}</Entity>`));
	}
	lines.push(line(2, "</BaseEntities>"));

	for (const ref of view.relationshipRefs ?? []) {
		const type = JOIN_TYPES.has(ref.type) ? ref.type : "INNER";
		lines.push(line(2, `<RelationshipRef type="${type}">${xmlText(ref.name)}</RelationshipRef>`));
	}

	if ((view.parameters ?? []).length) {
		lines.push(line(2, "<Parameters>"));
		for (const param of view.parameters) {
			const type = ["int", "float", "string"].includes(param.type) ? param.type : "string";
			const fallback = param.default == null || param.default === "" ? "" : ` default="${xmlAttr(param.default)}"`;
			lines.push(line(3, `<Parameter name="${xmlAttr(param.name)}" type="${type}"${fallback}/>`));
		}
		lines.push(line(2, "</Parameters>"));
	}

	if ((view.filters ?? []).length) {
		lines.push(line(2, "<Filters>"));
		for (const filter of view.filters) {
			lines.push(line(3, "<Filter>"));
			lines.push(line(4, `<Entity>${xmlText(filter.entity)}</Entity>`));
			if (filter.query) {
				lines.push(line(4, `<Query>${xmlText(filter.query)}</Query>`));
			} else if (filter.attribute) {
				lines.push(line(4, `<Attribute>${xmlText(filter.attribute)}</Attribute>`));
				lines.push(line(4, `<Operator>${xmlText(filter.operator || "=")}</Operator>`));
				lines.push(line(4, `<Value>${xmlText(filter.value ?? "")}</Value>`));
			}
			lines.push(line(3, "</Filter>"));
		}
		lines.push(line(2, "</Filters>"));
	}

	if ((view.groupBy ?? []).length || (view.measures ?? []).length) {
		lines.push(line(2, "<Aggregation>"));
		if ((view.groupBy ?? []).length) {
			lines.push(line(3, "<GroupBy>"));
			for (const item of view.groupBy) {
				lines.push(line(4, `<Attribute entity="${xmlAttr(item.entity)}">${xmlText(item.attribute)}</Attribute>`));
			}
			lines.push(line(3, "</GroupBy>"));
		}
		if ((view.measures ?? []).length) {
			lines.push(line(3, "<Measures>"));
			for (const measure of view.measures) {
				const entity = measure.entity ? ` entity="${xmlAttr(measure.entity)}"` : "";
				const attribute = measure.attribute ? ` attribute="${xmlAttr(measure.attribute)}"` : "";
				lines.push(line(4, `<Measure fn="${xmlAttr(measure.fn)}" alias="${xmlAttr(measure.alias)}"${entity}${attribute}/>`));
			}
			lines.push(line(3, "</Measures>"));
		}
		if ((view.having ?? []).length) {
			lines.push(line(3, "<Having>"));
			for (const item of view.having) {
				lines.push(line(4, `<Filter alias="${xmlAttr(item.alias)}" operator="${xmlAttr(item.operator || "=")}" value="${xmlAttr(item.value ?? "")}"/>`));
			}
			lines.push(line(3, "</Having>"));
		}
		lines.push(line(2, "</Aggregation>"));
	}

	if ((view.orderBy ?? []).length) {
		lines.push(line(2, "<OrderBy>"));
		for (const sort of view.orderBy) {
			const target = sort.alias
				? `alias="${xmlAttr(sort.alias)}"`
				: `entity="${xmlAttr(sort.entity)}" attribute="${xmlAttr(sort.attribute)}"`;
			const direction = sort.direction === "desc" ? "desc" : "asc";
			lines.push(line(3, `<Sort ${target} direction="${direction}"/>`));
		}
		lines.push(line(2, "</OrderBy>"));
	}

	if (view.limit != null && view.limit !== "") {
		lines.push(line(2, `<Limit>${Number(view.limit)}</Limit>`));
	}
	if (view.offset) {
		lines.push(line(2, `<Offset>${Number(view.offset)}</Offset>`));
	}

	lines.push(line(1, "</View>"));
	return lines.join("\n");
}

function viewPattern(name) {
	return new RegExp(`<View\\s+name="${escapeRegExp(xmlAttr(name))}"[^>]*>[\\s\\S]*?</View>`);
}

export function replaceViewBlock(viewsXml, previousName, block) {
	const pattern = viewPattern(previousName);
	if (previousName && pattern.test(viewsXml)) {
		return viewsXml.replace(pattern, () => block.trim());
	}
	if (viewsXml.includes("</Views>")) {
		return viewsXml.replace(/<\/Views>\s*$/, () => `${block}\n</Views>`);
	}
	return `<Views>\n${block}\n</Views>\n`;
}

export function replaceViewXml(viewsXml, view, previousName = view.name) {
	return replaceViewBlock(viewsXml, previousName, viewToXml(view));
}

export function removeViewXml(viewsXml, name) {
	return viewsXml.replace(new RegExp(`[ \\t]*${viewPattern(name).source}\\s*`), "");
}

export function viewNameFromXml(block) {
	const match = String(block ?? "").match(/<View\s+name="([^"]*)"/);
	return match
		? match[1].replaceAll("&quot;", '"').replaceAll("&lt;", "<").replaceAll("&amp;", "&")
		: "";
}
