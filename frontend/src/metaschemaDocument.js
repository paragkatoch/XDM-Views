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

function relationshipPattern(name) {
	return new RegExp(`[ \\t]*<Relationship\\s+name="${escapeRegExp(xmlAttr(name))}"[^>]*>[\\s\\S]*?</Relationship>\\n?`);
}

export function relationshipToXml({ name, leftEntity, leftAttribute, rightEntity, rightAttribute }) {
	return [
		`    <Relationship name="${xmlAttr(name)}" type="EQUI_JOIN">`,
		`      <LeftEntity>${xmlText(leftEntity)}</LeftEntity>`,
		`      <RightEntity>${xmlText(rightEntity)}</RightEntity>`,
		"      <Condition>",
		"        <Left>",
		`          <Entity>${xmlText(leftEntity)}</Entity>`,
		`          <Attribute>${xmlText(leftAttribute)}</Attribute>`,
		"        </Left>",
		"        <Operator>=</Operator>",
		"        <Right>",
		`          <Entity>${xmlText(rightEntity)}</Entity>`,
		`          <Attribute>${xmlText(rightAttribute)}</Attribute>`,
		"        </Right>",
		"      </Condition>",
		"    </Relationship>",
		"",
	].join("\n");
}

export function replaceRelationshipXml(metaschemaXml, relationship, previousName = "") {
	const block = relationshipToXml(relationship);
	const pattern = previousName ? relationshipPattern(previousName) : null;
	if (pattern && pattern.test(metaschemaXml)) {
		return metaschemaXml.replace(pattern, () => block);
	}
	if (/<\/Relationships>/.test(metaschemaXml)) {
		return metaschemaXml.replace(/([ \t]*)<\/Relationships>/, (_, indent) => `${block}${indent}</Relationships>`);
	}
	if (!/<\/MetaSchema>/.test(metaschemaXml)) {
		throw new Error("MetaSchema XML has no closing </MetaSchema> tag.");
	}
	return metaschemaXml.replace("</MetaSchema>", `  <Relationships>\n${block}  </Relationships>\n\n</MetaSchema>`);
}

export function removeRelationshipXml(metaschemaXml, name) {
	return metaschemaXml.replace(relationshipPattern(name), "");
}
