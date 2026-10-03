import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import DataTable from "../src/components/DataTable.vue";

const columns = [
  { key: "name", label: "名称", sortable: true },
  { key: "value", label: "数值", sortable: true, align: "right" as const },
  { key: "hash", label: "哈希", mono: true },
];

const rows = [
  { name: "b 折", value: 2, hash: "bbbb" },
  { name: "a 折", value: 10, hash: "aaaa" },
  { name: "c 折", value: 5, hash: null },
];

function mountTable(props: Record<string, unknown> = {}) {
  return mount(DataTable, { props: { columns, rows, ...props } });
}

describe("DataTable（纯展示表格）", () => {
  it("渲染列头与行；null 单元格回落为破折号", () => {
    const wrapper = mountTable();
    const header = wrapper.findAll("th").map((th) => th.text());
    expect(header).toEqual(["名称", "数值", "哈希"]);
    const cells = wrapper.findAll("tbody tr")[0].findAll("td").map((td) => td.text());
    expect(cells).toEqual(["b 折", "2", "bbbb"]);
    const thirdRow = wrapper.findAll("tbody tr")[2].findAll("td").map((td) => td.text());
    expect(thirdRow).toEqual(["c 折", "5", "—"]);
  });

  it("点击排序列：数值升序 → 再点降序，aria-sort 跟随", async () => {
    const wrapper = mountTable();
    const valueHeader = wrapper.findAll("th")[1];
    await valueHeader.trigger("click");
    let firstRow = wrapper.findAll("tbody tr")[0].findAll("td")[1].text();
    expect(firstRow).toBe("2");
    expect(valueHeader.attributes("aria-sort")).toBe("ascending");
    await valueHeader.trigger("click");
    firstRow = wrapper.findAll("tbody tr")[0].findAll("td")[1].text();
    expect(firstRow).toBe("10");
    expect(valueHeader.attributes("aria-sort")).toBe("descending");
  });

  it("字符串列按中文 locale 排序；不可排序列点击无效", async () => {
    const wrapper = mountTable();
    await wrapper.findAll("th")[0].trigger("click");
    const names = wrapper.findAll("tbody tr").map((tr) => tr.findAll("td")[0].text());
    expect(names).toEqual(["a 折", "b 折", "c 折"]);
    const hashHeader = wrapper.findAll("th")[2];
    await hashHeader.trigger("click");
    expect(hashHeader.attributes("aria-sort")).toBeUndefined();
  });

  it("空数据渲染空态单行，文案可配", () => {
    const wrapper = mountTable({ rows: [], emptyText: "尚无已发布实验" });
    expect(wrapper.get('[data-testid="data-table-empty"]').text()).toBe("尚无已发布实验");
  });

  it("右对齐与等宽类按列配置落在单元格上", () => {
    const wrapper = mountTable();
    expect(wrapper.findAll("th")[1].classes()).toContain("cell-right");
    expect(wrapper.findAll("tbody tr")[0].findAll("td")[2].classes()).toContain("cell-mono");
  });
});
