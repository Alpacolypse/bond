import { describe, expect, it } from "vitest";
import { describeAdded, isNameTaken } from "./sources";

describe("isNameTaken", () => {
  it("recognizes a taken name", () => {
    expect(isNameTaken("fax.pdf: the name fax is taken")).toBe(true);
    expect(isNameTaken("a document named fax already exists")).toBe(true);
    expect(isNameTaken("'smith' already has a document named 'fax'; add it with update")).toBe(true);
    expect(isNameTaken("cannot read fax.docx: unsupported format")).toBe(false);
  });
});

describe("describeAdded", () => {
  it("says what was added and what was already held", () => {
    expect(
      describeAdded({
        added: [
          { document: "fax", added: true },
          { document: "policy", added: false },
        ],
        note: null,
      }),
    ).toBe("Added fax; already held: policy.");
    expect(describeAdded({ added: [], note: null })).toBe("Nothing added.");
  });
});
