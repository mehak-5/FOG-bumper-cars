import {
  Alert,
  AppBar,
  Box,
  Button,
  Chip,
  Snackbar,
  Stack,
  Tab,
  Tabs,
  Toolbar,
  Typography,
} from '@mui/material';
import React, { useEffect, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { CameraManager } from './components/CameraManager';
import { ControlBar } from './components/ControlBar';
import { EventLogPanel } from './components/EventLogPanel';
import { HealthCheckModal } from './components/HealthCheckModal';
import { PreviewGrid } from './components/PreviewGrid';
import { ReadinessPanel } from './components/ReadinessPanel';
import { RideHistory } from './components/RideHistory';
import { Settings } from './components/Settings';
import { StatusBanner } from './components/StatusBanner';
import { TriggerPanel } from './components/TriggerPanel';
import {
  setDiskFreeGb,
  setNotice,
  setPreviewFrame,
  setReadiness,
  setState,
  type RootState,
} from './store';

function TabPanel({ value, index, children }: { value: number; index: number; children: React.ReactNode }) {
  return value === index ? <Box sx={{ pt: 2 }}>{children}</Box> : null;
}

export default function App() {
  const dispatch = useDispatch();
  const appState = useSelector((s: RootState) => s.state);
  const notice = useSelector((s: RootState) => s.notice);
  const previews = useSelector((s: RootState) => s.previews);
  const readiness = useSelector((s: RootState) => s.readiness);
  const diskFreeGb = useSelector((s: RootState) => s.diskFreeGb);
  const [tab, setTab] = useState(0);
  const [healthOpen, setHealthOpen] = useState(true); // auto-open health check modal on startup

  useEffect(() => {
    void window.bumper.getState().then((s) => dispatch(setState(s)));
    const offState = window.bumper.onState((s) => dispatch(setState(s)));
    const offNotice = window.bumper.onNotice((n) => dispatch(setNotice(n)));
    const offPreview = window.bumper.onPreviewFrame((d) => dispatch(setPreviewFrame(d)));
    const offReadiness = window.bumper.onReadiness((r) => dispatch(setReadiness(r)));
    const offDisk = window.bumper.onDiskStatus((d) => dispatch(setDiskFreeGb(d.freeGb)));

    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
      const key = e.key.toLowerCase();
      if (['s', 'e'].includes(key)) void window.bumper.pressKey(key);
    };
    window.addEventListener('keydown', onKey);

    return () => {
      offState(); offNotice(); offPreview(); offReadiness(); offDisk();
      window.removeEventListener('keydown', onKey);
    };
  }, [dispatch]);

  if (!appState) return <Typography sx={{ p: 4 }}>Starting…</Typography>;

  const { ride, venue, sessionId, startedAt, rideEndsAt, lastSession, cameras, trigger } = appState;
  const diskLow = diskFreeGb != null && diskFreeGb < 5;
  const idleCameras = cameras.map((c) => ({ id: c.id, name: c.name }));

  return (
    <>
      {/* ── Health Check Modal ─────────────────────────────────────── */}
      <HealthCheckModal
        open={healthOpen}
        onClose={() => setHealthOpen(false)}
        readiness={readiness}
      />

      <AppBar position="static" color="default" elevation={1}>
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>
            🚗 Bumper Car Moments — {venue}
          </Typography>
          <Chip
            size="small"
            color={ride === 'recording' ? 'error' : 'default'}
            label={ride.toUpperCase()}
          />
          {diskLow && (
            <Chip size="small" color="warning" label={`⚠ Disk: ${diskFreeGb?.toFixed(1)} GB`} />
          )}

          {/* Admin panel health check button */}
          <Button
            size="small"
            variant="outlined"
            onClick={() => setHealthOpen(true)}
            sx={{ ml: 1, whiteSpace: 'nowrap' }}
          >
            🩺 Health Check
          </Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ px: 3, py: 1 }}>
        <StatusBanner
          ride={ride}
          sessionId={sessionId}
          startedAt={startedAt}
          rideEndsAt={rideEndsAt ?? null}
          lastSession={lastSession}
        />
      </Box>

      <Box sx={{ borderBottom: 1, borderColor: 'divider', px: 3 }}>
        <Tabs value={tab} onChange={(_, v: number) => setTab(v)}>
          <Tab label="Console" />
          <Tab label="Camera Manager" />
          <Tab label="Settings" />
          <Tab label="Ride History" />
        </Tabs>
      </Box>

      <Box sx={{ px: 3, pb: 3 }}>
        <TabPanel value={tab} index={0}>
          <Stack spacing={2}>
            {ride === 'idle' && (
              <ReadinessPanel cameras={idleCameras} readiness={readiness} />
            )}
            <ControlBar ride={ride} readiness={readiness} onKey={(k) => void window.bumper.pressKey(k)} />
            <PreviewGrid cameras={cameras} ride={ride} previews={previews} readiness={readiness} />
            <TriggerPanel trigger={trigger} />
            <EventLogPanel />
          </Stack>
        </TabPanel>

        <TabPanel value={tab} index={1}>
          <CameraManager />
        </TabPanel>

        <TabPanel value={tab} index={2}>
          <Settings />
        </TabPanel>

        <TabPanel value={tab} index={3}>
          <RideHistory />
        </TabPanel>
      </Box>

      <Snackbar open={!!notice} autoHideDuration={4000} onClose={() => dispatch(setNotice(null))}>
        {notice ? <Alert severity={notice.level} variant="filled">{notice.text}</Alert> : undefined}
      </Snackbar>
    </>
  );
}
