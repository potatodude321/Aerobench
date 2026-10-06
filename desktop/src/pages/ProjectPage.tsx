import { Badge, Button, Container, Group, Paper, SimpleGrid, Stack, Table, Text, Textarea, TextInput, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { bridge } from "../api/bridge";
import { api } from "../api/client";
import { notifyError } from "../components/notify";
import { KeyValue, StatusBadge } from "../components/ui";
import { pid, useApp } from "../state/store";

export function ProjectPage() {
  const { project, patchConfig, closeProject, runs } = useApp();
  const cfg = project!.config;
  const [events, setEvents] = useState<any[]>([]);
  const [notes, setNotes] = useState(cfg.notes);
  const [name, setName] = useState(cfg.name);
  useEffect(() => {
    api.get(`/api/p/${pid()}/events?limit=100`).then(setEvents).catch(notifyError);
  }, [runs.length]);
  return (
    <Container size="xl" py="md" style={{ height: "100%", overflow: "auto" }}>
      <Group justify="space-between" mb="md">
        <Title order={3}>Project</Title>
        <Group>
          {bridge && (
            <Button variant="default" size="xs" onClick={() => bridge!.openPath(project!.root)}>
              Open folder
            </Button>
          )}
          <Button variant="subtle" size="xs" color="gray" onClick={closeProject}>
            Close project
          </Button>
        </Group>
      </Group>
      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Paper withBorder p="md" radius="md">
          <Stack gap="xs">
            <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} onBlur={() => name !== cfg.name && patchConfig({ name })} />
            <Textarea label="Notes" autosize minRows={4} value={notes} onChange={(e) => setNotes(e.currentTarget.value)} onBlur={() => notes !== cfg.notes && patchConfig({ notes })} />
            <KeyValue
              rows={[
                ["Project id", <span className="mono">{cfg.project_id}</span>],
                ["Folder", <span className="mono">{project!.root}</span>],
                ["Created", cfg.created_at],
                ["Modified", cfg.modified_at],
                ["Template", cfg.template_id || "—"],
                ["Schema version", cfg.schema_version],
                ["Configuration hash (solve)", <span className="mono">{project!.staleness.hashes.solve.slice(0, 16)}</span>],
              ]}
            />
            <Group gap={6}>
              <Text size="xs">Mesh</Text>
              <Badge color={project!.staleness.mesh.stale ? "orange" : "teal"} variant="light">
                {project!.staleness.mesh.current ? "up to date" : project!.staleness.mesh.stale ? "stale" : "not generated"}
              </Badge>
              <Text size="xs">Solution</Text>
              <Badge color={project!.staleness.solve.stale ? "orange" : "teal"} variant="light">
                {project!.staleness.solve.current ? "up to date" : project!.staleness.solve.stale ? "stale" : "not run"}
              </Badge>
            </Group>
          </Stack>
        </Paper>
        <Paper withBorder p="md" radius="md">
          <Text fw={700} mb="xs">
            Activity
          </Text>
          <Table className="compact">
            <Table.Tbody>
              {events.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td>
                    <Text size="xs" c="dimmed">
                      {e.t.replace("T", " ").slice(0, 19)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <StatusBadge status={e.kind.includes("interrupted") ? "interrupted" : undefined} />
                    <Text size="xs">{e.kind.replace(/_/g, " ")}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" className="mono">
                      {e.ref}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      </SimpleGrid>
    </Container>
  );
}
