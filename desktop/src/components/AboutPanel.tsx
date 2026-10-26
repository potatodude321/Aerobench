import { Anchor, Paper, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { BUNDLED, COPYRIGHT, LICENCE_NAME, OPENFOAM_ACKNOWLEDGEMENT, OPENFOAM_DISCLAIMER, REPO_URL } from "../about";

/** Settings -> About: version, licence, source code and the notices the bundled programs need. */
export function AboutPanel() {
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    api.get("/api/health").then((h: any) => setVersion(h?.version ?? null)).catch(() => setVersion(null));
  }, []);
  return (
    <Paper withBorder p="md" radius="md">
      <Text fw={700} mb="xs">
        About
      </Text>
      <Stack gap="xs">
        <Text size="sm">
          AeroBench {version ?? ""} is free and open-source software under the {LICENCE_NAME}. Source code, releases and the issue
          tracker:{" "}
          <Anchor href={REPO_URL} target="_blank" rel="noreferrer" size="sm">
            {REPO_URL.replace(/^https:\/\//, "")}
          </Anchor>
        </Text>
        <Text size="sm">
          {COPYRIGHT} AeroBench comes with ABSOLUTELY NO WARRANTY. It is free software, and you are welcome to redistribute
          and change it under the terms of the licence (LICENSE.txt in the installation folder). Projects, results and reports you
          make with it are yours to use as you like.
        </Text>
        <Text size="sm">
          It includes {BUNDLED.map(([n, l]) => `${n} (${l})`).join(", ")}. Their licence texts are in the installation folder
          (resources\licenses and resources\THIRD-PARTY-LICENSES.txt); their source code is published with each release.
        </Text>
        <Text size="sm">{OPENFOAM_DISCLAIMER}</Text>
        <Text size="sm">{OPENFOAM_ACKNOWLEDGEMENT}</Text>
      </Stack>
    </Paper>
  );
}
