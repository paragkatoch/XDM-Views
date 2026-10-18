export const PARAMETER_TYPE_LABELS = {
	string: "Text",
	int: "Whole number",
	float: "Decimal",
};

export const JOIN_OPTIONS = [
	{ value: "INNER", label: "Inner" },
	{ value: "LEFT", label: "Left" },
	{ value: "RIGHT", label: "Right" },
	{ value: "FULL", label: "Full" },
	{ value: "SEMI", label: "Semi" },
	{ value: "ANTI", label: "Anti" },
];

export function formatParameterLabel(name) {
	const words = {
		id: "ID",
		min: "Minimum",
		max: "Maximum",
	};

	return String(name ?? "")
		.split("_")
		.filter(Boolean)
		.map((word) => words[word] ?? word.charAt(0).toUpperCase() + word.slice(1))
		.join(" ");
}

export function formatParameterHint(param) {
	const typeLabel = PARAMETER_TYPE_LABELS[param.type] ?? param.type;
	if (param.default == null || param.default === "") {
		return typeLabel;
	}

	return `${typeLabel} · Default ${param.default}`;
}

export function formatEntityName(name) {
	return String(name ?? "").replace(/([a-z])([A-Z])/g, "$1 $2");
}

export function formatFilterCondition(filter) {
	if (filter.query) {
		const condition = filter.query.match(/\[([\s\S]+)\]/);
		return condition ? condition[1].trim() : filter.query.trim();
	}

	return `${filter.attribute} ${filter.operator} ${filter.value}`;
}

export function joinDescription(type) {
	const descriptions = {
		INNER: "Only rows that match on both sides",
		LEFT: "Every left row, with matching right data",
		RIGHT: "Every right row, with matching left data",
		FULL: "Every row from both sides",
		SEMI: "Left rows that have a match",
		ANTI: "Left rows that do not have a match",
	};
	return descriptions[type] ?? "";
}

export function orderRelationships(entityNames, relationships) {
	const selected = new Set(entityNames);
	const pool = (relationships ?? []).filter(
		(relationship) => selected.has(relationship.left_entity) && selected.has(relationship.right_entity),
	);
	if (entityNames.length <= 1) {
		return { joins: [], missing: [] };
	}

	const included = new Set(entityNames[0] ? [entityNames[0]] : []);
	const joins = [];
	const unused = [...pool];
	let moved = true;
	while (moved) {
		moved = false;
		for (let index = 0; index < unused.length; index += 1) {
			const relationship = unused[index];
			const leftIn = included.has(relationship.left_entity);
			const rightIn = included.has(relationship.right_entity);
			if (leftIn === rightIn) {
				continue;
			}
			included.add(relationship.left_entity);
			included.add(relationship.right_entity);
			joins.push(relationship);
			unused.splice(index, 1);
			moved = true;
			break;
		}
	}

	return {
		joins,
		missing: entityNames.filter((name) => !included.has(name)),
	};
}
