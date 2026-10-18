import { orderRelationships } from "./format";

export function emptyView(name) {
	return {
		name,
		baseEntities: [],
		projection: {},
		relationshipRefs: [],
		parameters: [],
		filters: [],
		groupBy: [],
		measures: [],
		having: [],
		orderBy: [],
		limit: null,
		offset: 0,
	};
}

export function uniqueViewName(names, base = "New view") {
	let name = base;
	let suffix = 2;
	while (names.includes(name)) {
		name = `${base} ${suffix}`;
		suffix += 1;
	}
	return name;
}

export function withEntities(view, entityNames, entities, relationships) {
	const known = new Map((entities ?? []).map((entity) => [entity.name, entity]));
	const picked = entityNames.filter((name) => known.has(name));
	const keep = new Set(picked);
	const chain = orderRelationships(picked, relationships);
	const previousTypes = Object.fromEntries((view.relationshipRefs ?? []).map((ref) => [ref.name, ref.type || "INNER"]));
	const projection = {};
	for (const name of picked) {
		const available = known.get(name)?.attributes ?? [];
		const current = (view.projection?.[name] ?? []).filter((attribute) => available.includes(attribute));
		projection[name] = current.length ? current : [...available];
	}
	return {
		...view,
		baseEntities: picked,
		projection,
		relationshipRefs: chain.joins.map((relationship) => ({
			name: relationship.name,
			type: previousTypes[relationship.name] || "INNER",
		})),
		filters: (view.filters ?? []).filter((filter) => keep.has(filter.entity)),
		groupBy: (view.groupBy ?? []).filter((item) => keep.has(item.entity)),
		measures: (view.measures ?? []).filter((measure) => !measure.entity || keep.has(measure.entity)),
		orderBy: (view.orderBy ?? []).filter((sort) => sort.alias || keep.has(sort.entity)),
	};
}

export function toggleEntity(view, name, entities, relationships) {
	const current = view.baseEntities ?? [];
	const next = current.includes(name) ? current.filter((item) => item !== name) : [...current, name];
	return withEntities(view, next, entities, relationships);
}

export function toggleColumn(view, entityName, attribute, entities) {
	const available = entities.find((entity) => entity.name === entityName)?.attributes ?? [];
	const selected = view.projection?.[entityName] ?? [...available];
	const next = selected.includes(attribute) ? selected.filter((item) => item !== attribute) : [...selected, attribute];
	if (!next.length) {
		return view;
	}
	return { ...view, projection: { ...view.projection, [entityName]: next } };
}

export function setColumns(view, entityName, columns) {
	return { ...view, projection: { ...view.projection, [entityName]: [...columns] } };
}

export function joinsOutdated(view, relationships) {
	const link = orderRelationships(view.baseEntities ?? [], relationships);
	const current = (view.relationshipRefs ?? []).map((ref) => ref.name);
	return (
		!link.missing.length &&
		link.joins.length > 0 &&
		(current.length !== link.joins.length || current.some((name) => !relationships.some((item) => item.name === name)))
	);
}
