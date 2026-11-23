// Markdown rendered from mdast, with highlights split per text node.
//
// Marks are UTF-16 ranges over the raw content, and mdast positions are UTF-16 offsets into the
// same string, so each text node renders the slice of marks over its own source range. Spans that
// cross inline markup (emphasis, links, table cells) therefore highlight correctly in pieces.

import type { Nodes, Table } from "mdast";
import { useMemo, type ReactNode } from "react";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import { unified } from "unified";
import type { Mark } from "../nav/marks";
import { MarkedText, type MarkClick } from "./MarkedText";

const parser = unified().use(remarkParse).use(remarkGfm);

type Ctx = { source: string; marks: readonly Mark[]; onMark: MarkClick };

export function MarkdownViewer({
  content,
  marks,
  onMark,
  sourceView,
}: {
  content: string;
  marks: readonly Mark[];
  onMark: MarkClick;
  sourceView: boolean;
}) {
  const tree = useMemo(() => parser.parse(content), [content]);
  if (sourceView) {
    return (
      <pre className="md-source">
        <MarkedText source={content} from={0} to={content.length} marks={marks} onMark={onMark} />
      </pre>
    );
  }
  const ctx: Ctx = { source: content, marks, onMark };
  return <div className="md">{renderChildren(tree, ctx)}</div>;
}

/** A leaf's value with its marks, located inside the node's source range. */
function markedLeaf(value: string, node: Nodes, ctx: Ctx): ReactNode {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  if (start === undefined || end === undefined) return value;
  const at = ctx.source.slice(start, end).indexOf(value);
  if (at !== -1) {
    return <MarkedText source={ctx.source} from={start + at} to={start + at + value.length} marks={ctx.marks} onMark={ctx.onMark} />;
  }
  // The value differs from its source (escapes, entities): mark it as a whole by the first mark it touches.
  const m = ctx.marks.find((m) => m.start < end && start < m.end && (m.sourcing !== null || m.target));
  if (!m) return value;
  return <MarkedText source={value} from={0} to={value.length} marks={[{ ...m, start: 0, end: value.length }]} onMark={ctx.onMark} />;
}

function renderChildren(node: { children: Nodes[] }, ctx: Ctx): ReactNode[] {
  return node.children.map((c, i) => renderNode(c, ctx, i));
}

function renderNode(node: Nodes, ctx: Ctx, key: number): ReactNode {
  switch (node.type) {
    case "root":
      return <div key={key}>{renderChildren(node, ctx)}</div>;
    case "paragraph":
      return <p key={key}>{renderChildren(node, ctx)}</p>;
    case "heading": {
      const H = `h${node.depth}` as const;
      return <H key={key}>{renderChildren(node, ctx)}</H>;
    }
    case "text":
      return <span key={key}>{markedLeaf(node.value, node, ctx)}</span>;
    case "emphasis":
      return <em key={key}>{renderChildren(node, ctx)}</em>;
    case "strong":
      return <strong key={key}>{renderChildren(node, ctx)}</strong>;
    case "delete":
      return <del key={key}>{renderChildren(node, ctx)}</del>;
    case "inlineCode":
      return <code key={key}>{markedLeaf(node.value, node, ctx)}</code>;
    case "code":
      return (
        <pre key={key}>
          <code>{markedLeaf(node.value, node, ctx)}</code>
        </pre>
      );
    case "html":
      return <code key={key}>{markedLeaf(node.value, node, ctx)}</code>;
    case "blockquote":
      return <blockquote key={key}>{renderChildren(node, ctx)}</blockquote>;
    case "list": {
      const L = node.ordered ? "ol" : "ul";
      return (
        <L key={key} start={node.start ?? undefined}>
          {renderChildren(node, ctx)}
        </L>
      );
    }
    case "listItem":
      return (
        <li key={key}>
          {node.checked != null && <input type="checkbox" checked={node.checked} readOnly />}
          {node.children.map((c, i) =>
            // Tight list items hold their paragraph without a <p> wrapper.
            c.type === "paragraph" && node.spread === false ? <span key={i}>{renderChildren(c, ctx)}</span> : renderNode(c, ctx, i),
          )}
        </li>
      );
    case "table":
      return renderTable(node, ctx, key);
    case "link":
      return (
        <a key={key} href={node.url} title={node.title ?? undefined} target="_blank" rel="noreferrer">
          {renderChildren(node, ctx)}
        </a>
      );
    case "image":
      return (
        <span key={key} className="muted">
          [image: {markedLeaf(node.alt ?? "", node, ctx)}]
        </span>
      );
    case "break":
      return <br key={key} />;
    case "thematicBreak":
      return <hr key={key} />;
    case "footnoteReference":
      return <sup key={key}>[{node.label ?? node.identifier}]</sup>;
    case "footnoteDefinition":
      return (
        <div key={key} className="footnote">
          <sup>[{node.label ?? node.identifier}]</sup> {renderChildren(node, ctx)}
        </div>
      );
    case "linkReference":
      return <span key={key}>{renderChildren(node, ctx)}</span>;
    default:
      // definitions, image references, front matter: not shown.
      return null;
  }
}

function renderTable(node: Table, ctx: Ctx, key: number): ReactNode {
  const [head, ...body] = node.children;
  const align = (i: number) => node.align?.[i] ?? undefined;
  return (
    <table key={key}>
      {head && (
        <thead>
          <tr>
            {head.children.map((cell, i) => (
              <th key={i} style={{ textAlign: align(i) }}>
                {renderChildren(cell, ctx)}
              </th>
            ))}
          </tr>
        </thead>
      )}
      <tbody>
        {body.map((row, r) => (
          <tr key={r}>
            {row.children.map((cell, i) => (
              <td key={i} style={{ textAlign: align(i) }}>
                {renderChildren(cell, ctx)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
