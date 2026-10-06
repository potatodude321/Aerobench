import { Alert, Badge, Button, Group, MultiSelect, Paper, SegmentedControl, Select, Stack, Switch, Table, Text, Textarea, TextInput } from "@mantine/core";
import { IconDownload, IconExternalLink, IconFileTypePdf, IconFolder, IconReport } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { bridge, isDesktop } from "../api/bridge";
import { api } from "../api/client";
import { notifyError, notifyInfo } from "../components/notify";
import { EmptyState, PageLayout, SectionTitle } from "../components/ui";
import { pid, useApp } from "../state/store";

export function ReportPage() {
  const { runs, project } = useApp();
  const completed = runs.filter((r) => ["completed", "failed"].includes(r.status));
  const [kind, setKind] = useState("run");
  const [runIds, setRunIds] = useState<string[]>(completed.slice(0, 1).map((r) => r.id));
  const [baseline, setBaseline] = useState<string | null>(null);
  const [studies, setStudies] = useState<any[]>([]);
  const [studyId, setStudyId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [images, setImages] = useState(true);
  const [reports, setReports] = useState<any[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => api.get<any[]>(`/api/p/${pid()}/reports`).then((r) => { setReports(r); if (!sel && r[0]) setSel(r[0].id); }).catch(notifyError);
  useEffect(() => {
    load();
    api.get<any[]>(`/api/p/${pid()}/studies`).then((s) => { setStudies(s); if (s[0]) setStudyId(s[0].id); }).catch(notifyError);
  }, []);

  const generate = async () => {
    setBusy(true);
    try {
      const body: any = { kind, title: title || null, notes, images };
      if (kind === "run") body.run_ids = runIds.slice(0, 1);
      if (kind === "compare") {
        body.run_ids = runIds;
        body.baseline = baseline && runIds.includes(baseline) ? baseline : runIds[0];
      }
      if (kind === "study") body.study_id = studyId;
      const r = await api.post(`/api/p/${pid()}/reports`, body);
      await load();
      setSel(r.id);
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  const current = reports.find((r) => r.id === sel);
  const url = current ? api.imageUrl(`/api/p/${pid()}/reports/${current.id}/report.html`) : "";
  const exportPdf = async () => {
    if (!current || !bridge) return;
    const out = await bridge.saveFile({ title: "Export report as PDF", defaultPath: `${(current.title || current.id).replace(/[^\w\- ]+/g, "_")}.pdf`, filters: [{ name: "PDF", extensions: ["pdf"] }] });
    if (!out) return;
    try {
      const r = await bridge.printToPDF(url, out);
      notifyInfo(`Saved ${r.path}`, "PDF exported");
    } catch (e) {
      notifyError(e);
    }
  };
  const downloadCsv = async () => {
    if (!current) return;
    const text = await api.get<string>(`/api/p/${pid()}/reports/${current.id}/data.csv`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    a.download = `${current.id}.csv`;
    a.click();
  };

  const runOpts = completed.map((r) => ({ value: r.id, label: `${r.label} (${r.id})` }));
  const side = (
    <Stack gap="xs">
      <SectionTitle>New report</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={kind} onChange={setKind} data={[{ value: "run", label: "Single run" }, { value: "compare", label: "Comparison" }, { value: "study", label: "Study / optimisation" }]} />
      {kind === "run" && <Select size="xs" label="Run" data={runOpts} value={runIds[0] || null} onChange={(v) => setRunIds(v ? [v] : [])} searchable />}
      {kind === "compare" && (
        <>
          <MultiSelect size="xs" label="Runs" data={runOpts} value={runIds} onChange={setRunIds} searchable />
          <Select size="xs" label="Baseline" data={runIds.map((i) => runOpts.find((o) => o.value === i) || { value: i, label: i })} value={baseline} onChange={setBaseline} placeholder="first selected" />
        </>
      )}
      {kind === "study" && <Select size="xs" label="Study" data={studies.map((s) => ({ value: s.id, label: `${s.name} — ${s.kind} (${s.status})` }))} value={studyId} onChange={setStudyId} />}
      <TextInput size="xs" label="Title" placeholder="automatic" value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
      <Textarea size="xs" label="Notes" autosize minRows={2} value={notes} onChange={(e) => setNotes(e.currentTarget.value)} />
      {kind !== "study" && <Switch size="xs" label="Include ParaView flow images" checked={images} onChange={(e) => setImages(e.currentTarget.checked)} />}
      <Button size="xs" leftSection={<IconReport size={14} />} onClick={generate} loading={busy}
        disabled={(kind === "run" && !runIds.length) || (kind === "compare" && runIds.length < 2) || (kind === "study" && !studyId)}>
        Generate report
      </Button>
      <Text size="xs" c="dimmed">
        Reports are self-contained HTML files (charts and images embedded) stored in the project's reports folder, with the data as CSV.
        {isDesktop ? " Export to PDF prints the report to A4." : " Use the browser's print dialog for PDF."}
      </Text>
      <SectionTitle>Reports</SectionTitle>
      {!reports.length && <Text size="xs" c="dimmed">No reports yet.</Text>}
      <Table className="compact" highlightOnHover>
        <Table.Tbody>
          {reports.map((r) => (
            <Table.Tr key={r.id} onClick={() => setSel(r.id)} style={{ cursor: "pointer", background: sel === r.id ? "rgba(34,184,207,0.08)" : undefined }}>
              <Table.Td>
                <Text size="xs" fw={600}>{r.title}</Text>
                <Text size="xs" c="dimmed">{r.created_at.replace("T", " ").slice(0, 16)}</Text>
              </Table.Td>
              <Table.Td w={80}><Badge size="xs" variant="outline">{r.kind}</Badge></Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );

  return (
    <PageLayout side={side} sideWidth={400}>
      {current ? (
        <Stack gap={6} p="sm" h="100%">
          <Group justify="space-between">
            <Text fw={700}>{current.title}</Text>
            <Group gap={6}>
              {isDesktop && <Button size="xs" leftSection={<IconFileTypePdf size={14} />} onClick={exportPdf}>Export PDF</Button>}
              <Button size="xs" variant="default" leftSection={<IconDownload size={14} />} onClick={downloadCsv}>CSV</Button>
              <Button size="xs" variant="default" leftSection={<IconExternalLink size={14} />} onClick={() => window.open(url, "_blank")}>Open</Button>
              {isDesktop && project && (
                <Button size="xs" variant="default" leftSection={<IconFolder size={14} />} onClick={() => bridge?.showItem(`${project.root}/${current.path}/report.html`)}>Show file</Button>
              )}
            </Group>
          </Group>
          {current.status !== "completed" && <Alert color="red">{current.message}</Alert>}
          <Paper withBorder style={{ flex: 1, minHeight: 0, overflow: "hidden", background: "#fff" }}>
            <iframe title="report" src={url} style={{ border: 0, width: "100%", height: "100%" }} />
          </Paper>
        </Stack>
      ) : (
        <EmptyState title="No report selected">Generate a run, comparison or study report on the left.</EmptyState>
      )}
    </PageLayout>
  );
}
