/*
 * Extend the managed Zotero tree to cover the user's existing economics and
 * management library. Items may belong to more than one thematic collection.
 * The catch-all collection is rebuilt from active, genuinely unclassified items.
 */

(async () => {
const libraryID = Zotero.Libraries.userLibraryID;
const report = { classifierVersion: "2026-07-17b", created: [], categoryCounts: {}, unclassified: 0 };

const root = Zotero.Collections.getByLibrary(libraryID).find(
  (collection) => !collection.deleted && !collection.parentID && collection.name === "研究文献",
);
if (!root) throw new Error("Managed root collection not found: 研究文献");

async function ensureRootChild(name) {
  const existing = root
    .getChildCollections(false, true)
    .find((collection) => !collection.deleted && collection.name === name);
  if (existing) return existing;
  const collection = new Zotero.Collection();
  collection.libraryID = libraryID;
  collection.name = name;
  collection.parentID = root.id;
  await collection.saveTx();
  report.created.push(name);
  return collection;
}

async function addItems(collection, itemIDs) {
  const ids = [...new Set(itemIDs.filter(Boolean))];
  if (!ids.length) return;
  await Zotero.DB.executeTransaction(async () => {
    await collection.addItems(ids);
  });
}

const categories = {
  innovation: await ensureRootChild("07 创新政策、研发与专利"),
  transfer: await ensureRootChild("08 科技转移与区域创新"),
  procurement: await ensureRootChild("09 政府采购与需求侧政策"),
  finance: await ensureRootChild("10 公司金融、融资与投资"),
  uncertainty: await ensureRootChild("11 不确定性与企业决策"),
  standards: await ensureRootChild("12 标准、知识产权与开放创新"),
  industrial: await ensureRootChild("13 产业政策与地方发展"),
  management: await ensureRootChild("14 企业管理、生产率与组织"),
  medicalAI: await ensureRootChild("15 医学教育与人工智能"),
  green: await ensureRootChild("16 绿色创新与环境治理"),
  documents: await ensureRootChild("17 政策法规与统计资料"),
};

const methods = root
  .getChildCollections(false, true)
  .find((collection) => collection.name === "90 数据、分类与方法");
const legacyCategories = {
  algorithmsLegacy: root
    .getChildCollections(false, true)
    .find((collection) => collection.name === "02 算法定价与竞争"),
  informationLegacy: root
    .getChildCollections(false, true)
    .find((collection) => collection.name === "03 信息设计、学习与博弈"),
};
const missing = root
  .getChildCollections(false, true)
  .find((collection) => collection.name === "98 缺少全文");
const unclassified = root
  .getChildCollections(false, true)
  .find((collection) => collection.name === "99 待核验与未分类");

function cleanText(value) {
  return String(value || "")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

function itemText(item) {
  return cleanText([
    item.getField("title"),
    item.getField("abstractNote"),
    item.getField("publicationTitle"),
    item.getTags().map((tag) => tag.tag).join(" "),
  ].join(" "));
}

const rules = {
  innovation: /(innovation|innovative|r d|research grant|research subsidy|public research|basic research|patent|technology policy|科技创新|企业创新|研发|研究资助|创新补贴|政府补贴|科技资助|基础研究|专利)/,
  transfer: /(technology transfer|knowledge transfer|knowledge spillover|regional innovation|innovation network|science park|cluster|agglomeration|technology diffusion|科技成果转化|技术转移|区域创新|创新网络|科技园区|中关村|概念验证|中试|集聚|扩散)/,
  procurement: /(public procurement|government procurement|government demand|demand side policy|sbir|政府采购|需求侧政策|采购创新|采购合作创新|需求释放)/,
  finance: /(financing|financial friction|credit ration|credit constraint|venture capital|corporate investment|cash holding|working capital|bank competition|self dealing|融资约束|公司金融|企业投资|固定资产投资|现金持有|营运资本|银行业竞争|风险投资|民营化)/,
  uncertainty: /(uncertainty|political risk|policy risk|economic policy uncertainty|trade policy uncertainty|不确定性|政治风险|政策风险)/,
  standards: /(standardization|standardisation|standards and innovation|economics of standards|intellectual property|property rights protection|open source|知识产权|技术标准|标准化|开放创新)/,
  industrial: /(industrial policy|place based|place-based|local economic development|enterprise zone|regional policy|special deals|growing like china|产业政策|地方政策|区域政策|城市群政策|政府扶持|产业发展|新能源)/,
  management: /(management practice|management matter|organizational|organisational|productivity|firm performance|business performance|consulting service|sme|misallocation|企业管理|组织学习|生产率|企业绩效|管理咨询|资源错配)/,
  medicalAI: /(medical education|medical examination|radiology resident|multiple choice question|multiple-choice question|clinical education|医学教育|医学考试|放射科|选择题)/,
  green: /(green innovation|environmental innovation|environmental governance|carbon|绿色创新|环境治理|低碳|碳排放)/,
  documents: /(statistical bulletin|regulation|government plan|action plan|interim measures|统计公报|建设方案|工作方案|建设规划|条例|管理办法|国务院|人民政府|科技部)/,
  methods: /(difference in differences|event study|synthetic control|equivalence test|weak instrument|two way fixed effects|causal inference|identification|econometric|pyblp|回归不连续|双重差分|事件研究|合成控制|因果推断|识别方法)/,
  algorithmsLegacy: /(algorithmic pricing|pricing algorithms|algorithmic collusion|reinforcement learning|q learning)/,
  informationLegacy: /(bayes correlated equilibrium|information structures in games|job market signaling|use of knowledge in society|bayesian persuasion)/,
};

const items = (await Zotero.Items.getAll(libraryID, true)).filter(
  (item) => item.isRegularItem() && !item.deleted,
);
const assignments = new Map(Object.keys(categories).map((key) => [key, []]));
assignments.set("methods", []);
for (const key of Object.keys(legacyCategories)) assignments.set(key, []);

for (const item of items) {
  const text = itemText(item);
  for (const [key, pattern] of Object.entries(rules)) {
    if (pattern.test(text)) assignments.get(key).push(item.id);
  }
}

for (const [key, collection] of Object.entries(categories)) {
  await addItems(collection, assignments.get(key));
  report.categoryCounts[collection.name] = assignments.get(key).length;
}
if (methods) {
  await addItems(methods, assignments.get("methods"));
  report.categoryCounts[methods.name] = assignments.get("methods").length;
}
for (const [key, collection] of Object.entries(legacyCategories)) {
  if (!collection) continue;
  await addItems(collection, assignments.get(key));
  report.categoryCounts[collection.name] = assignments.get(key).length;
}

const excluded = new Set([missing && missing.id, unclassified && unclassified.id].filter(Boolean));
const descendants = [];
function collectDescendants(parent) {
  for (const child of parent.getChildCollections(false, true)) {
    if (child.deleted) continue;
    descendants.push(child);
    collectDescendants(child);
  }
}
collectDescendants(root);
const substantiveIDs = new Set(
  descendants
    .filter((collection) => !excluded.has(collection.id))
    .map((collection) => collection.id),
);

if (unclassified) {
  const oldIDs = unclassified.getChildItems(true, true);
  if (oldIDs.length) {
    await Zotero.DB.executeTransaction(async () => {
      await unclassified.removeItems(oldIDs);
    });
  }
  const substantiveIDList = [...substantiveIDs];
  const placeholders = substantiveIDList.map(() => "?").join(",");
  const classifiedItemIDs = new Set(
    substantiveIDList.length
      ? await Zotero.DB.columnQueryAsync(
          `SELECT DISTINCT itemID FROM collectionItems WHERE collectionID IN (${placeholders})`,
          substantiveIDList,
        )
      : [],
  );
  report.classifiedBeforeCatchAll = classifiedItemIDs.size;
  report.substantiveCollectionCount = substantiveIDList.length;
  const remaining = items.filter((item) => !classifiedItemIDs.has(item.id));
  await addItems(unclassified, remaining.map((item) => item.id));
  report.unclassified = remaining.length;
}

return JSON.stringify(report, null, 2);
})();
