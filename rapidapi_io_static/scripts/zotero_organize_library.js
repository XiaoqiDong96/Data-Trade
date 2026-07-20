/*
 * Run from Zotero: Tools -> Developer -> Run JavaScript.
 *
 * The script is intentionally idempotent. It creates a stable collection tree,
 * classifies existing references without deleting anything, imports a curated
 * data/API-market bibliography by DOI or ISBN, and asks Zotero to retrieve
 * legally available full text for items that do not yet have a PDF.
 */

(async () => {
const libraryID = Zotero.Libraries.userLibraryID;
const report = {
  startedAt: new Date().toISOString(),
  collectionsCreated: 0,
  existingItemsClassified: 0,
  referencesMatched: 0,
  referencesImported: 0,
  referencesFailed: [],
  pdfsPresentBefore: 0,
  pdfsDownloaded: 0,
  pdfDownloadFailed: [],
  missingFullText: 0,
};

function cleanText(value) {
  return String(value || "")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/https?:\/\/(?:dx\.)?doi\.org\//g, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

function normalizeDOI(value) {
  const text = String(value || "").trim().toLowerCase();
  const match = text.match(/10\.\d{4,9}\/[-._;()/:a-z0-9]+/i);
  return match ? match[0].replace(/[.,;]+$/, "") : "";
}

function itemDOI(item) {
  return normalizeDOI(item.getField("DOI") || item.getField("extra"));
}

function itemISBN(item) {
  return String(item.getField("ISBN") || "")
    .replace(/[^0-9Xx]/g, "")
    .toUpperCase();
}

function titleKey(item) {
  return cleanText(item.getField("title"));
}

function collectionParentID(collection) {
  return collection.parentID || null;
}

function allCollections() {
  const found = [];
  function visit(collection) {
    if (!collection || collection.deleted) return;
    found.push(collection);
    for (const child of collection.getChildCollections(false, true)) visit(child);
  }
  for (const rootCollection of Zotero.Collections.getByLibrary(libraryID)) {
    visit(rootCollection);
  }
  return found;
}

async function ensureCollection(name, parentID = null) {
  const existing = allCollections().find(
    (collection) =>
      collection.name === name &&
      collectionParentID(collection) === (parentID || null),
  );
  if (existing) return existing;

  const collection = new Zotero.Collection();
  collection.libraryID = libraryID;
  collection.name = name;
  if (parentID) collection.parentID = parentID;
  await collection.saveTx();
  report.collectionsCreated += 1;
  return collection;
}

async function addToCollection(collection, itemIDs) {
  const ids = [...new Set(itemIDs.filter(Boolean))];
  if (!ids.length) return;
  await Zotero.DB.executeTransaction(async () => {
    await collection.addItems(ids);
  });
}

async function hasLocalPDF(item) {
  for (const attachmentID of item.getAttachments()) {
    const attachment = Zotero.Items.get(attachmentID);
    if (!attachment) continue;
    const isPDF =
      (typeof attachment.isPDFAttachment === "function" &&
        attachment.isPDFAttachment()) ||
      String(attachment.attachmentContentType || "").toLowerCase() ===
        "application/pdf";
    if (!isPDF) continue;
    try {
      if (await attachment.fileExists()) return true;
    } catch (error) {
      // A stored PDF attachment is still useful when a linked-file check fails.
      if (
        attachment.attachmentLinkMode !==
        Zotero.Attachments.LINK_MODE_LINKED_URL
      ) {
        return true;
      }
    }
  }
  return false;
}

const root = await ensureCollection("研究文献");
const project = await ensureCollection("01 数据交易与 API 市场", root.id);
const collections = {
  dataEconomics: await ensureCollection(
    "01 数据经济学与非竞争性",
    project.id,
  ),
  dataPricing: await ensureCollection("02 数据定价、交易与估值", project.id),
  apiMarkets: await ensureCollection("03 API 市场与访问合同", project.id),
  infoGoods: await ensureCollection("04 信息商品、版本化与复制", project.id),
  platforms: await ensureCollection("05 平台、搜索与排序", project.id),
  blp: await ensureCollection("06 BLP、需求与识别", project.id),
  trials: await ensureCollection("07 免费试用、披露与学习", project.id),
  projectCore: await ensureCollection("08 核心文献（待精读）", project.id),
  algorithms: await ensureCollection("02 算法定价与竞争", root.id),
  information: await ensureCollection("03 信息设计、学习与博弈", root.id),
  aiLabor: await ensureCollection("04 人工智能、劳动与技能", root.id),
  family: await ensureCollection("05 人口、生育与家庭", root.id),
  chinaHousehold: await ensureCollection("06 中国家庭金融与劳动力", root.id),
  methods: await ensureCollection("90 数据、分类与方法", root.id),
  missing: await ensureCollection("98 缺少全文", root.id),
  unclassified: await ensureCollection("99 待核验与未分类", root.id),
};

const collectionByKey = {
  dataEconomics: collections.dataEconomics,
  dataPricing: collections.dataPricing,
  apiMarkets: collections.apiMarkets,
  infoGoods: collections.infoGoods,
  platforms: collections.platforms,
  blp: collections.blp,
  trials: collections.trials,
};

const curatedReferences = [
  // Differentiated-products demand, supply, and identification
  { doi: "10.2307/2555829", title: "Estimating Discrete-Choice Models of Product Differentiation", folders: ["blp"] },
  { doi: "10.2307/2171802", title: "Automobile Prices in Market Equilibrium", folders: ["blp"] },
  { doi: "10.1111/1468-0262.00194", title: "Measuring Market Power in the Ready-to-Eat Cereal Industry", folders: ["blp"] },
  { doi: "10.3982/ECTA9027", title: "Identification in Differentiated Products Markets Using Market Level Data", folders: ["blp"] },
  { doi: "10.1016/j.jeconom.2013.12.001", title: "Improving the Performance of Random Coefficients Demand Models: The Role of Optimal Instruments", folders: ["blp"] },
  { doi: "10.3982/ECTA10600", title: "Large Market Asymptotics for Differentiated Product Demand Estimators with Economic Models of Supply", folders: ["blp"] },
  { doi: "10.3386/w26375", title: "Measuring Substitution Patterns in Differentiated Products Industries", folders: ["blp"] },
  { doi: "10.1111/1756-2171.12352", title: "Best Practices for Differentiated Products Demand Estimation with PyBLP", folders: ["blp"] },
  { doi: "10.1016/j.jeconom.2024.105926", title: "Incorporating Micro Data into Differentiated Products Demand Estimation with PyBLP", folders: ["blp"] },
  { doi: "10.1257/aer.102.2.643", title: "The Welfare Effects of Bundling in Multichannel Television Markets", folders: ["blp", "infoGoods"] },
  { doi: "10.1146/annurev-economics-080218-025643", title: "Weak Instruments in Instrumental Variables Regression: Theory and Practice", folders: ["blp"] },

  // Platforms, search, and marketplace design
  { doi: "10.1162/154247603322493212", title: "Platform Competition in Two-Sided Markets", folders: ["platforms"] },
  { doi: "10.1111/j.1756-2171.2006.tb00037.x", title: "Competition in Two-Sided Markets", folders: ["platforms"] },
  { doi: "10.1111/j.1756-2171.2006.tb00036.x", title: "Two-Sided Markets: A Progress Report", folders: ["platforms"] },
  { doi: "10.1287/mnsc.1050.0400", title: "Two-Sided Network Effects: A Theory of Information Product Design", folders: ["platforms", "infoGoods"] },
  { doi: "10.1257/aer.100.4.1642", title: "A Price Theory of Multi-Sided Platforms", folders: ["platforms"] },
  { doi: "10.1016/j.ijindorg.2015.03.003", title: "Multi-sided platforms", folders: ["platforms"] },
  { doi: "10.1257/aer.20171218", title: "Consumer Price Search and Platform Design in Internet Commerce", folders: ["platforms"] },
  { doi: "10.1287/mksc.2017.1072", title: "The Power of Rankings: Quantifying the Effect of Rankings on Online Consumer Search and Purchase Decisions", folders: ["platforms"] },
  { doi: "10.1257/aer.102.6.2955", title: "Testing Models of Consumer Search Using Data on Web Browsing and Purchasing Behavior", folders: ["platforms"] },
  { doi: "10.1287/orsc.1110.0678", title: "Let a Thousand Flowers Bloom? An Early Look at Large Numbers of Software App Developers and Patterns of Innovation", folders: ["platforms"] },

  // Information goods, versioning, copying, disclosure, and trials
  { doi: "10.1287/mnsc.45.12.1613", title: "Bundling Information Goods: Pricing, Profits, and Efficiency", folders: ["infoGoods"] },
  { doi: "10.1287/mksc.19.1.63.15182", title: "Bundling and Competition on the Internet", folders: ["infoGoods"] },
  { doi: "10.1086/467420", title: "Shared Information Goods", folders: ["infoGoods"] },
  { doi: "10.1080/07421222.2001.11045681", title: "Information Goods and Vertical Differentiation", folders: ["infoGoods"] },
  { doi: "10.1287/mnsc.1040.0291", title: "Nonlinear Pricing of Information Goods", folders: ["infoGoods"] },
  { doi: "10.1111/1467-6451.00133", title: "Versioning Information Goods", folders: ["infoGoods"] },
  { doi: "10.1257/aer.20161079", title: "The Limits of Price Discrimination", folders: ["infoGoods", "dataPricing"] },
  { isbn: "9780875848631", title: "Information Rules: A Strategic Guide to the Network Economy", folders: ["infoGoods"] },
  { doi: "10.1086/259630", title: "Information and Consumer Behavior", folders: ["trials"] },
  { doi: "10.1086/466995", title: "The Informational Role of Warranties and Private Disclosure about Product Quality", folders: ["trials"] },
  { doi: "10.1287/mksc.2015.0973", title: "Try It, You'll Like It-Or Will You? The Perils of Early Free-Trial Promotions for High-Tech Service Adoption", folders: ["trials"] },

  // Economics of data
  { doi: "10.1257/mic.20200200", title: "Too Much Data: Prices and Inefficiencies in Data Markets", folders: ["dataEconomics", "dataPricing"] },
  { doi: "10.1257/jel.54.2.442", title: "The Economics of Privacy", folders: ["dataEconomics"] },
  { doi: "10.1257/pandp.20181003", title: "Should We Treat Data as Labor? Moving Beyond Free", folders: ["dataEconomics", "dataPricing"] },
  { doi: "10.1146/annurev-economics-080315-015439", title: "Markets for Information: An Introduction", folders: ["dataEconomics", "dataPricing"] },
  { doi: "10.1257/aer.20230478", title: "Data, Competition, and Digital Privacy", folders: ["dataEconomics"] },
  { doi: "10.1111/1756-2171.12407", title: "The Economics of Social Data", folders: ["dataEconomics"] },
  { doi: "10.1287/mnsc.2021.3986", title: "Data Sharing and Data Markets", folders: ["dataEconomics", "dataPricing"] },
  { doi: "10.1111/1756-2171.12382", title: "Competing Data Intermediaries", folders: ["dataEconomics", "platforms"] },
  { doi: "10.1016/j.jet.2021.105316", title: "Data Externalities and Socially Optimal Policies", folders: ["dataEconomics"] },
  { doi: "10.1257/aer.20191330", title: "Nonrivalry and the Economics of Data", folders: ["dataEconomics"] },
  { doi: "10.1016/j.jfineco.2025.104053", title: "Data Sales and Data Dilution", folders: ["dataEconomics", "dataPricing"] },
  { doi: "10.1093/rfs/hhae034", title: "Valuing Financial Data", folders: ["dataPricing"] },
  { doi: "10.1093/restud/rdag074", title: "A Model of the Data Economy", folders: ["dataEconomics"] },
  { doi: "10.1257/jel.20171452", title: "Digital Economics", folders: ["dataEconomics", "platforms"] },
  { doi: "10.1093/rof/rfac073", title: "Valuing Data as an Asset", folders: ["dataPricing"] },
  { doi: "10.1257/jel.20221580", title: "Data and the Aggregate Economy", folders: ["dataEconomics"] },

  // Data and API marketplaces
  { doi: "10.1145/3565011.3569053", title: "Data Marketplaces and the Data Economy", folders: ["apiMarkets", "dataPricing"] },
  { doi: "10.1109/ICDE55515.2023.00300", title: "A Survey of Data Marketplaces and Their Business Models", folders: ["apiMarkets", "dataPricing"] },
  { doi: "10.1093/icc/dtaa002", title: "Markets for Data", folders: ["apiMarkets", "dataPricing"] },
  { doi: "10.1007/s40595-016-0064-2", title: "Data Marketplaces: Trends and Monetisation of Data Goods", folders: ["apiMarkets", "dataPricing"] },
  { doi: "10.1016/j.csi.2024.103878", title: "Pricing4APIs: A Framework for API Pricing", folders: ["apiMarkets", "dataPricing"] },
];

function classifyItem(item) {
  const tags = item
    .getTags()
    .map((tag) => tag.tag)
    .join(" ");
  const haystack = cleanText(
    [
      item.getField("title"),
      item.getField("abstractNote"),
      item.getField("publicationTitle"),
      tags,
    ].join(" "),
  );
  const targets = [];

  const matches = (pattern) => pattern.test(haystack);
  if (
    matches(/\b(data econom|data market|data trad|data pric|data asset|data sales|data dilution|nonrival|privacy|information good|api market|api pric|data platform|digital data|数据交易|数据要素|数据市场|数据定价)\b/)
  ) {
    targets.push(collections.projectCore);
  }
  if (
    matches(/\b(algorithmic pric|algorithmic collusion|pricing algorithm|ai agent|reinforcement learning|q learning|cournot|tacit collusion)\b/)
  ) {
    targets.push(collections.algorithms);
  }
  if (
    matches(/\b(bayesian persuasion|information design|information structure|blackwell|social learning|public information|private information|learning in games|game theory|信号博弈|信息设计)\b/)
  ) {
    targets.push(collections.information);
  }
  if (
    matches(/\b(artificial intelligence|generative ai|large language model|chatgpt|gpt |automation|robot|labor market|labour market|occupation|worker|employment|skill|task content|人工智能|机器人|劳动力|就业|技能)\b/)
  ) {
    targets.push(collections.aiLabor);
  }
  if (
    matches(/\b(fertility|marriage|childbearing|family size|reproductive|birth rate|motherhood|parenthood|生育|婚姻|家庭规模|子女)\b/)
  ) {
    targets.push(collections.family);
  }
  if (
    matches(/\b(chfs|cfps|cgss|china household|chinese household|household debt|household leverage|migrant worker|flexible employment|job quality|social capital|中国家庭|家庭金融|家庭负债|农民工|灵活就业)\b/)
  ) {
    targets.push(collections.chinaHousehold);
  }
  if (
    matches(/\b(o net|occupational classification|crosswalk|record linkage|string distance|data cleaning|database|measurement error|causal inference|econometric method|变量定义|数据清洗|分类标准|识别方法)\b/)
  ) {
    targets.push(collections.methods);
  }
  return [...new Map(targets.map((target) => [target.id, target])).values()];
}

let regularItems = (await Zotero.Items.getAll(libraryID, true)).filter((item) =>
  item.isRegularItem() && !item.deleted,
);

for (const item of regularItems) {
  const targets = classifyItem(item);
  if (!targets.length) {
    await addToCollection(collections.unclassified, [item.id]);
    continue;
  }
  for (const target of targets) await addToCollection(target, [item.id]);
  report.existingItemsClassified += 1;
}

let doiIndex = new Map();
let isbnIndex = new Map();
let titleIndex = new Map();

function indexItem(item) {
  const doi = itemDOI(item);
  const isbn = itemISBN(item);
  const title = titleKey(item);
  if (doi) doiIndex.set(doi, item);
  if (isbn) isbnIndex.set(isbn, item);
  if (title) titleIndex.set(title, item);
}

for (const item of regularItems) indexItem(item);

async function importReference(reference) {
  const doi = normalizeDOI(reference.doi);
  const isbn = String(reference.isbn || "").replace(/[^0-9Xx]/g, "").toUpperCase();
  const title = cleanText(reference.title);
  let item =
    (doi && doiIndex.get(doi)) ||
    (isbn && isbnIndex.get(isbn)) ||
    (title && titleIndex.get(title));

  if (item) {
    report.referencesMatched += 1;
  } else {
    const translate = new Zotero.Translate.Search();
    translate.setIdentifier(doi ? { DOI: doi } : { ISBN: isbn });
    const translators = await translate.getTranslators();
    if (!translators.length) throw new Error("No identifier translator found");
    translate.setTranslator(translators);
    const newItems = await translate.translate({
      libraryID,
      collections: [collections.projectCore.id],
      saveAttachments: true,
    });
    item = newItems.find((candidate) => candidate.isRegularItem());
    if (!item) throw new Error("Identifier lookup returned no bibliographic item");
    report.referencesImported += 1;
    indexItem(item);
  }

  await addToCollection(collections.projectCore, [item.id]);
  for (const folderKey of reference.folders) {
    await addToCollection(collectionByKey[folderKey], [item.id]);
  }
  if (!item.getTags().some((tag) => tag.tag === "项目：数据交易")) {
    item.addTag("项目：数据交易", 1);
    await item.saveTx({ skipSelect: true });
  }
  return item;
}

const projectItemIDs = [];
for (const reference of curatedReferences) {
  try {
    const item = await importReference(reference);
    projectItemIDs.push(item.id);
  } catch (error) {
    report.referencesFailed.push({
      identifier: reference.doi || reference.isbn,
      title: reference.title,
      error: String(error),
    });
  }
}

regularItems = (await Zotero.Items.getAll(libraryID, true)).filter((item) =>
  item.isRegularItem() && !item.deleted,
);

for (const item of regularItems) {
  if (await hasLocalPDF(item)) report.pdfsPresentBefore += 1;
}

// Project references are attempted first; the remainder of the library follows.
const priority = new Set(projectItemIDs);
const downloadQueue = [
  ...regularItems.filter((item) => priority.has(item.id)),
  ...regularItems.filter((item) => !priority.has(item.id)),
];

for (const item of downloadQueue) {
  if (await hasLocalPDF(item)) continue;
  const doi = itemDOI(item);
  const url = String(item.getField("url") || "").trim();
  if (!doi && !url) continue;
  try {
    const attachment = await Zotero.Attachments.addAvailableFile(item);
    if (attachment && (await hasLocalPDF(item))) report.pdfsDownloaded += 1;
  } catch (error) {
    report.pdfDownloadFailed.push({
      itemID: item.id,
      title: item.getField("title"),
      doi,
      error: String(error),
    });
  }
}

const missingItemIDs = [];
for (const item of regularItems) {
  if (!(await hasLocalPDF(item))) missingItemIDs.push(item.id);
}
const staleMissingItemIDs = collections.missing.getChildItems(true, true);
if (staleMissingItemIDs.length) {
  await Zotero.DB.executeTransaction(async () => {
    await collections.missing.removeItems(staleMissingItemIDs);
  });
}
await addToCollection(collections.missing, missingItemIDs);
report.missingFullText = missingItemIDs.length;
report.finishedAt = new Date().toISOString();

return JSON.stringify(report, null, 2);
})();
