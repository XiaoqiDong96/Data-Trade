/*
 * Repair helper for the managed "研究文献" tree.
 *
 * It merges duplicate managed collections without deleting their items, then
 * uses Zotero's native merge routine for duplicate project references. Only
 * items tagged "项目：数据交易" are eligible for bibliographic merging.
 */

(async () => {
const libraryID = Zotero.Libraries.userLibraryID;
const report = {
  collectionsMerged: 0,
  itemGroupsMerged: 0,
  duplicateItemsMerged: 0,
  failures: [],
};

function normalizeDOI(value) {
  const match = String(value || "")
    .toLowerCase()
    .match(/10\.\d{4,9}\/[-._;()/:a-z0-9]+/i);
  return match ? match[0].replace(/[.,;]+$/, "") : "";
}

function normalizeTitle(value) {
  return String(value || "")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

async function addItems(collection, itemIDs) {
  const ids = [...new Set(itemIDs.filter(Boolean))];
  if (!ids.length) return;
  await Zotero.DB.executeTransaction(async () => {
    await collection.addItems(ids);
  });
}

async function moveOrMergeChildren(source, target) {
  await addItems(target, source.getChildItems(true, true));

  for (const sourceChild of source.getChildCollections(false, true)) {
    const targetChild = target
      .getChildCollections(false, true)
      .find((candidate) => candidate.name === sourceChild.name);
    if (targetChild) {
      await moveOrMergeChildren(sourceChild, targetChild);
      continue;
    }
    sourceChild.parentID = target.id;
    await sourceChild.saveTx({ skipSelect: true });
  }

  await source.eraseTx({ deleteItems: false, skipSelect: true });
  report.collectionsMerged += 1;
}

async function mergeDuplicateChildren(parent) {
  const groups = new Map();
  for (const child of parent.getChildCollections(false, true)) {
    const group = groups.get(child.name) || [];
    group.push(child);
    groups.set(child.name, group);
  }

  for (const group of groups.values()) {
    group.sort((a, b) => a.id - b.id);
    const keeper = group[0];
    for (const duplicate of group.slice(1)) {
      try {
        await moveOrMergeChildren(duplicate, keeper);
      } catch (error) {
        report.failures.push({
          type: "collection",
          name: duplicate.name,
          id: duplicate.id,
          error: String(error),
        });
      }
    }
    await mergeDuplicateChildren(keeper);
  }
}

const researchRoots = Zotero.Collections.getByLibrary(libraryID)
  .filter((collection) => !collection.parentID && collection.name === "研究文献")
  .sort((a, b) => a.id - b.id);

if (researchRoots.length) {
  const root = researchRoots[0];
  for (const duplicateRoot of researchRoots.slice(1)) {
    await moveOrMergeChildren(duplicateRoot, root);
  }
  await mergeDuplicateChildren(root);
}

let items = (await Zotero.Items.getAll(libraryID, true)).filter(
  (item) =>
    item.isRegularItem() &&
    !item.deleted &&
    item.getTags().some((tag) => tag.tag === "项目：数据交易"),
);

const groups = new Map();
for (const item of items) {
  const doi = normalizeDOI(item.getField("DOI") || item.getField("extra"));
  const title = normalizeTitle(item.getField("title"));
  const key = doi ? `doi:${doi}` : title ? `title:${title}` : "";
  if (!key) continue;
  const group = groups.get(key) || [];
  group.push(item);
  groups.set(key, group);
}

async function itemScore(item) {
  let score = 0;
  for (const attachmentID of item.getAttachments()) {
    const attachment = Zotero.Items.get(attachmentID);
    if (!attachment) continue;
    const isPDF =
      (typeof attachment.isPDFAttachment === "function" &&
        attachment.isPDFAttachment()) ||
      String(attachment.attachmentContentType || "").toLowerCase() ===
        "application/pdf";
    if (isPDF) score += 100;
  }
  score += item.getAttachments().length * 5;
  score += item.getNotes().length;
  return score;
}

for (const [key, group] of groups) {
  if (group.length < 2) continue;
  try {
    const ranked = [];
    for (const item of group) ranked.push({ item, score: await itemScore(item) });
    ranked.sort((a, b) => b.score - a.score || a.item.id - b.item.id);
    const master = ranked[0].item;
    const duplicates = ranked.slice(1).map((entry) => entry.item);
    await Zotero.Items.merge(master, duplicates);
    report.itemGroupsMerged += 1;
    report.duplicateItemsMerged += duplicates.length;
  } catch (error) {
    report.failures.push({ type: "item", key, error: String(error) });
  }
}

return JSON.stringify(report, null, 2);
})();
