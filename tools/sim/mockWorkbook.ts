// Minimal mock of the ExcelScript.Workbook API surface actually used by
// office-scripts/*.ts, backed by an in-memory array-of-arrays per sheet.
// Purpose: run the REAL compiled engine code (not a reimplementation)
// end-to-end against real production data, chaining engines in sequence so
// each one's writes are visible to the next - the closest verification
// possible without live Excel. See tools/sim/run_e2e.ts.

export type CellValue = string | number | boolean;
export type Row = CellValue[];

function parseA1StartRow(a1: string): number {
  // Only needs to handle patterns actually used in this codebase, e.g.
  // "A2:Q200000", "A1:F500", "A1:AM100000" - extracts the 1-indexed start
  // row from the first cell reference and converts to a 0-indexed row.
  const m = a1.match(/^[A-Z]+(\d+):/);
  if (!m) {
    throw new Error(`mockWorkbook: cannot parse A1 range "${a1}"`);
  }
  return parseInt(m[1], 10) - 1;
}

class MockRange {
  constructor(private ws: MockWorksheet, private startRow: number, private startCol: number, private data: Row[]) {}

  getValues(): Row[] {
    return this.data.map((r) => [...r]);
  }

  getRowCount(): number {
    return this.data.length;
  }

  setValues(values: Row[]): void {
    for (let i = 0; i < values.length; i++) {
      const targetRow = this.startRow + i;
      while (this.ws.data.length <= targetRow) {
        this.ws.data.push([]);
      }
      const row = this.ws.data[targetRow];
      for (let j = 0; j < values[i].length; j++) {
        row[this.startCol + j] = values[i][j];
      }
    }
  }

  setValue(value: CellValue): void {
    this.setValues([[value]]);
  }

  clear(): void {
    // Truncates from startRow onward (matches every clear() call site in
    // this codebase, which always clears "from row X to the end").
    this.ws.data.length = Math.min(this.ws.data.length, this.startRow);
  }
}

class MockWorksheet {
  data: Row[] = [];

  constructor(initial: Row[]) {
    this.data = initial.map((r) => [...r]);
  }

  getUsedRange(): MockRange | null {
    if (this.data.length === 0) {
      return null;
    }
    return new MockRange(this, 0, 0, this.data);
  }

  getRange(a1: string): MockRange {
    const startRow = parseA1StartRow(a1);
    // A "range to clear" doesn't need real data attached - clear() only
    // needs to know where to truncate from.
    return new MockRange(this, startRow, 0, []);
  }

  getRangeByIndexes(startRow: number, startCol: number, numRows: number, numCols: number): MockRange {
    return new MockRange(this, startRow, startCol, []);
  }
}

export class MockWorkbook {
  private sheets: { [name: string]: MockWorksheet } = {};

  constructor(seed: { [sheetName: string]: Row[] }) {
    for (const name of Object.keys(seed)) {
      this.sheets[name] = new MockWorksheet(seed[name]);
    }
  }

  getWorksheet(name: string): MockWorksheet {
    if (!this.sheets[name]) {
      throw new Error(`mockWorkbook: sheet "${name}" does not exist in the seed state`);
    }
    return this.sheets[name];
  }

  dump(): { [sheetName: string]: Row[] } {
    let out: { [sheetName: string]: Row[] } = {};
    for (const name of Object.keys(this.sheets)) {
      out[name] = this.sheets[name].data.map((r) => [...r]);
    }
    return out;
  }
}
