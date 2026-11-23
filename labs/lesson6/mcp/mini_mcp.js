#!/usr/bin/env node
// mini_mcp.js — the smallest useful MCP server: no dependencies, ~100 lines, speaks MCP over stdio.
//
// An MCP server is a program that reads JSON messages (one per line) on stdin and writes JSON messages
// (one per line) on stdout.  The client (pi) sends 3 kinds of messages that matter here:
//   initialize   -> "who are you, what can you do?"           -> we answer with our name and capabilities
//   tools/list   -> "which tools do you have?"                -> we answer with name + description + inputSchema
//   tools/call   -> "run this tool with these arguments"      -> we answer with some text
// Nothing else is needed for a server that only offers tools.
//
// Environment (set by the lab, or by you):
//   MINI_MCP_TOOLS   how many tools to offer (default 6, max 40)
//   MINI_MCP_DELAY   milliseconds to wait before answering `initialize` (default 0); used to see what pi does
//                    with a server that connects late
//   MINI_MCP_LOG     if set, a file to append one line per incoming message to (method + id)
"use strict";
const fs = require("fs");
const readline = require("readline");

const COUNT = Math.max(1, Math.min(40, parseInt(process.env.MINI_MCP_TOOLS || "6", 10) || 6));
const DELAY = Math.max(0, parseInt(process.env.MINI_MCP_DELAY || "0", 10) || 0);
const LOG = process.env.MINI_MCP_LOG || "";

// 40 notebook-style tools.  Every description is one plain English sentence (about 15-20 words) and every
// input schema has 2-3 string/integer properties, which is what a small real server looks like.
const NAMES = [
  ["list_notes", "List the notes in the notebook, newest first, optionally only those carrying a given tag."],
  ["get_note", "Return the full text, tags and timestamps of one note, looked up by its numeric identifier."],
  ["search_notes", "Search every note for a phrase and return the identifiers and first lines of the best matches."],
  ["create_note", "Create a new note with a title and a body, and return the identifier it was given."],
  ["update_note", "Replace the title or the body of an existing note, leaving its tags and history untouched."],
  ["delete_note", "Delete one note permanently after the caller has confirmed the identifier is the right one."],
  ["tag_note", "Attach a short tag to a note so that it can be found again with the list and search tools."],
  ["untag_note", "Remove a tag from a note without touching the rest of the note or the other tags."],
  ["list_tags", "List every tag in use together with the number of notes that carry it."],
  ["rename_tag", "Rename a tag everywhere it is used, merging it with an existing tag of the new name."],
  ["export_notes", "Export all notes, or only those with a given tag, as one markdown document and return its text."],
  ["import_notes", "Import notes from a markdown document, one note per top level heading, and return how many were added."],
  ["note_stats", "Return counts of notes, words and tags, and the date of the oldest and the newest note."],
  ["pin_note", "Pin a note so that it is always listed first, regardless of its age."],
  ["unpin_note", "Remove the pin from a note so that it is listed by date again."],
  ["archive_note", "Move a note to the archive, where it stays searchable but is hidden from the normal list."],
  ["restore_note", "Move a note from the archive back to the normal list, keeping its tags and identifier."],
  ["list_archive", "List the archived notes, newest first, optionally only those carrying a given tag."],
  ["link_notes", "Create a link between two notes so that each shows the other in its list of related notes."],
  ["unlink_notes", "Remove the link between two notes without changing either of the two notes themselves."],
  ["related_notes", "Return the notes linked to a given note, together with the first line of each of them."],
  ["note_history", "Return the list of earlier versions of a note, each with its timestamp and a short summary."],
  ["restore_version", "Replace the current text of a note with one of its earlier versions, keeping the newer one in history."],
  ["duplicate_note", "Create a copy of a note under a new identifier, with the same tags and a new title."],
  ["merge_notes", "Merge two notes into one by appending the second body to the first and deleting the second note."],
  ["split_note", "Split a note into several notes at every second level heading and return the new identifiers."],
  ["set_reminder", "Attach a reminder date to a note so that it shows up in the reminders list on that day."],
  ["clear_reminder", "Remove the reminder from a note without changing the note or its tags in any other way."],
  ["list_reminders", "List the notes that have a reminder, ordered by the date of the reminder, soonest first."],
  ["daily_digest", "Build a short digest of the notes that were created or changed on a given day."],
  ["weekly_digest", "Build a short digest of the notes that were created or changed during a given week."],
  ["find_duplicates", "Find notes whose text is almost the same and return the pairs, most similar first."],
  ["word_count", "Count the words of one note, or of all notes with a given tag, and return the totals."],
  ["set_color", "Give a note one of eight colors so that it stands out in the list of notes."],
  ["list_colors", "List the colors that can be given to a note together with the number of notes using each."],
  ["share_note", "Create a read only link to a note that anyone who has the link can open."],
  ["unshare_note", "Disable the read only link of a note so that it can no longer be opened by anybody."],
  ["list_shared", "List the notes that currently have a read only link, together with the link of each of them."],
  ["empty_trash", "Permanently remove every note that has been in the trash for more than thirty days."],
  ["trash_note", "Move a note to the trash, where it stays for thirty days before it is removed for good."],
];

// The five tools that really work have schemas that fit what they do; every other tool gets a rotating generic schema.
const REAL = {
  list_notes: { props: { tag: { type: "string", description: "Only list notes with this tag." }, limit: { type: "integer", description: "Maximum number of results to return." } }, required: [] },
  get_note: { props: { id: { type: "integer", description: "Numeric identifier of the note." } }, required: ["id"] },
  search_notes: { props: { text: { type: "string", description: "Phrase to look for in titles and bodies." }, limit: { type: "integer", description: "Maximum number of results to return." } }, required: ["text"] },
  list_tags: { props: {}, required: [] },
  note_stats: { props: {}, required: [] },
};

function schemaFor(i, name) {
  if (REAL[name]) return { type: "object", properties: REAL[name].props, required: REAL[name].required };
  // a rotating set of 2-3 properties, so the schemas are not all identical
  const props = { id: { type: "integer", description: "Numeric identifier of the note." } };
  const required = ["id"];
  const k = i % 4;
  if (k === 0) { props.tag = { type: "string", description: "Only consider notes with this tag." }; }
  if (k === 1) { props.text = { type: "string", description: "Text to use for this operation." }; required.push("text"); }
  if (k === 2) { props.limit = { type: "integer", description: "Maximum number of results to return." }; props.tag = { type: "string", description: "Only consider notes with this tag." }; }
  if (k === 3) { props.date = { type: "string", description: "Date in YYYY-MM-DD format." }; }
  return { type: "object", properties: props, required };
}

const TOOLS = NAMES.slice(0, COUNT).map(([name, description], i) => ({
  name, description, inputSchema: schemaFor(i, name), annotations: { readOnlyHint: /^(list|get|search|note|related|find|word|daily|weekly)_/.test(name) },
}));


// A tiny fixed notebook.  Five tools really work on it (list_notes, get_note, search_notes, list_tags, note_stats);
// the other tools only exist to make the tool list realistic: they answer "not implemented in this demo" and report an
// error, so a model that calls one knows at once that it got no data.
const NOTES = [
  { id: 1, title: "Team standup", tags: ["meeting"], body: "Standup moved to 09:30 on Tuesdays. Bring the sprint board." },
  { id: 2, title: "Release checklist", tags: ["release", "ops"], body: "Tag the build, update the changelog, notify support, and archive the old artifacts." },
  { id: 3, title: "Invoice 4417", tags: ["finance"], body: "Invoice 4417 from Brightwell is unpaid; the due date is the 30th. Ask accounting before paying." },
  { id: 4, title: "Reading list", tags: ["personal"], body: "Three books to finish this quarter: one on compilers, one on statistics, one on gardens." },
  { id: 5, title: "Server migration", tags: ["ops", "plan"], body: "Move the staging database first, then the web tier. Keep the old host for two weeks." },
  { id: 6, title: "Customer call notes", tags: ["meeting", "sales"], body: "The customer wants a quote by Friday and asked about volume discounts for next year." },
  { id: 7, title: "Budget draft", tags: ["finance", "plan"], body: "Travel budget is 20 percent over; cut the conference trip or postpone the training." },
  { id: 8, title: "Lunch ideas", tags: ["personal"], body: "Noodle place on the corner, the bakery with the good rye, or leftovers from Sunday." },
];

function run(name, a) {
  const tagOk = (n) => !a.tag || n.tags.includes(String(a.tag));
  if (name === "list_notes") return { text: NOTES.filter(tagOk).map((n) => n.id + ": " + n.title).join("\n") };
  if (name === "get_note") {
    const n = NOTES.find((x) => x.id === Number(a.id));
    return n ? { text: "id: " + n.id + "\ntitle: " + n.title + "\ntags: " + n.tags.join(", ") + "\nbody: " + n.body } : { text: "no note with id " + a.id, isError: true };
  }
  if (name === "search_notes") {
    const q = String(a.text || a.query || a.tag || "").toLowerCase();
    const hits = NOTES.filter((n) => q && (n.title + " " + n.body).toLowerCase().includes(q));
    return { text: hits.length ? hits.map((n) => n.id + ": " + n.title).join("\n") : "no matches" };
  }
  if (name === "list_tags") {
    const c = {};
    NOTES.forEach((n) => n.tags.forEach((t) => { c[t] = (c[t] || 0) + 1; }));
    return { text: Object.keys(c).sort().map((t) => t + ": " + c[t]).join("\n") };
  }
  if (name === "note_stats") return { text: NOTES.length + " notes, " + NOTES.reduce((s, n) => s + n.body.split(/\s+/).length, 0) + " words" };
  return { text: "not implemented in this demo: " + name, isError: true };
}

function send(obj) { process.stdout.write(JSON.stringify(obj) + "\n"); }
function note(line) { if (LOG) { try { fs.appendFileSync(LOG, line + "\n"); } catch (e) { /* ignore */ } } }

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  line = line.trim();
  if (!line) return;
  let msg;
  try { msg = JSON.parse(line); } catch (e) { return; }
  note((msg.method || "(response)") + " " + (msg.id === undefined ? "-" : msg.id));
  const id = msg.id;
  switch (msg.method) {
    case "initialize": {
      const reply = () => send({ jsonrpc: "2.0", id, result: {
        protocolVersion: (msg.params && msg.params.protocolVersion) || "2025-06-18",
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "mini-notes", version: "1.0.0" },
        instructions: "A small notebook server: " + COUNT + " tools for reading and changing notes.",
      } });
      if (DELAY > 0) setTimeout(reply, DELAY); else reply();
      break;
    }
    case "tools/list":
      send({ jsonrpc: "2.0", id, result: { tools: TOOLS } });
      break;
    case "tools/call": {
      const name = msg.params && msg.params.name;
      const args = (msg.params && msg.params.arguments) || {};
      const known = TOOLS.some((t) => t.name === name);
      let text, isError = !known;
      if (!known) text = "unknown tool " + name;
      else { const r = run(name, args); text = r.text; isError = !!r.isError; }
      send({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text }], isError } });
      break;
    }
    case "ping":
      send({ jsonrpc: "2.0", id, result: {} });
      break;
    default:
      if (id !== undefined && msg.method) send({ jsonrpc: "2.0", id, error: { code: -32601, message: "method not found: " + msg.method } });
  }
});
rl.on("close", () => process.exit(0));
