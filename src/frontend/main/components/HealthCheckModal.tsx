/**
 * HealthCheckModal
 * ----------------
 * Sequential health check panel shown at startup and accessible from the
 * Admin tab.  Three checks run in order; a failing check blocks the next.
 *
 *  1. Internet       — 9 attempts, 1.5 s apart
 *  2. Moments Server — 3 attempts, 2 s apart  (only if internet passed)
 *  3. Cameras        — all configured cameras must be ready (only if server passed)
 */
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import HourglassTopIcon from '@mui/icons-material/HourglassTop';
import PendingIcon from '@mui/icons-material/Pending';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { CameraReadiness, HealthCheckResult, HealthStatus } from '../../../backend/shared/types';

// -------------------------------------------------------------------------
// Types
// -------------------------------------------------------------------------

interface CheckState {
  status: HealthStatus;
  attempt: number;
  maxAttempts: number;
  message: string;
  alertText: string | null;
}

const INITIAL: CheckState = {
  status: 'pending',
  attempt: 0,
  maxAttempts: 1,
  message: '',
  alertText: null,
};

// -------------------------------------------------------------------------
// Sub-component: one check row
// -------------------------------------------------------------------------
function CheckRow({
  label,
  state,
  description,
}: {
  label: string;
  state: CheckState;
  description: string;
}) {
  const { status, attempt, maxAttempts, message, alertText } = state;

  const icon = (() => {
    switch (status) {
      case 'pass':    return <CheckCircleIcon color="success" />;
      case 'fail':    return <ErrorIcon color="error" />;
      case 'checking': return <CircularProgress size={22} thickness={5} />;
      case 'skipped': return <HourglassTopIcon color="disabled" />;
      default:        return <PendingIcon color="disabled" />;
    }
  })();

  const progress = status === 'checking' ? (attempt / maxAttempts) * 100 : 0;

  return (
    <Box sx={{ mb: 1.5 }}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Box sx={{ width: 24, display: 'flex', alignItems: 'center' }}>{icon}</Box>
        <Box sx={{ flex: 1 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Typography variant="subtitle2" fontWeight={600}>
              {label}
            </Typography>
            {status === 'checking' && (
              <Typography variant="caption" color="text.secondary">
                Attempt {attempt}/{maxAttempts}
              </Typography>
            )}
            {(status === 'pass' || status === 'fail') && (
              <Typography
                variant="caption"
                color={status === 'pass' ? 'success.main' : 'error.main'}
              >
                {message}
              </Typography>
            )}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {description}
          </Typography>
          {status === 'checking' && (
            <LinearProgress
              variant="determinate"
              value={progress}
              sx={{ mt: 0.5, height: 4, borderRadius: 2 }}
            />
          )}
        </Box>
      </Stack>
      {alertText && (
        <Alert severity="error" sx={{ mt: 1, py: 0.5 }}>
          {alertText}
        </Alert>
      )}
    </Box>
  );
}

// -------------------------------------------------------------------------
// Camera check row (uses live readiness data)
// -------------------------------------------------------------------------
function CameraCheckRow({
  state,
  cameraNames,
  readiness,
}: {
  state: CheckState;
  cameraNames: { id: string; name: string }[];
  readiness: Record<string, CameraReadiness>;
}) {
  const readyCount = cameraNames.filter((c) => readiness[c.id]?.ready).length;
  const total = cameraNames.length;

  return (
    <Box sx={{ mb: 1.5 }}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Box sx={{ width: 24, display: 'flex', alignItems: 'center' }}>
          {state.status === 'checking' ? (
            <CircularProgress size={22} thickness={5} />
          ) : state.status === 'pass' ? (
            <CheckCircleIcon color="success" />
          ) : state.status === 'fail' ? (
            <ErrorIcon color="error" />
          ) : state.status === 'skipped' ? (
            <HourglassTopIcon color="disabled" />
          ) : (
            <PendingIcon color="disabled" />
          )}
        </Box>
        <Box sx={{ flex: 1 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Typography variant="subtitle2" fontWeight={600}>
              Camera Check
            </Typography>
            {state.status !== 'pending' && state.status !== 'skipped' && (
              <Typography
                variant="caption"
                color={state.status === 'pass' ? 'success.main' : state.status === 'fail' ? 'error.main' : 'text.secondary'}
              >
                {readyCount}/{total} ready
              </Typography>
            )}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            All {total} configured cameras must be active
          </Typography>
          {state.status === 'checking' && cameraNames.length > 0 && (
            <Stack spacing={0.5} sx={{ mt: 0.75 }}>
              {cameraNames.map((cam) => {
                const r = readiness[cam.id];
                const ready = r?.ready ?? false;
                return (
                  <Stack key={cam.id} direction="row" spacing={1} alignItems="center">
                    {ready ? (
                      <CheckCircleIcon color="success" sx={{ fontSize: 14 }} />
                    ) : (
                      <CircularProgress size={14} />
                    )}
                    <Typography variant="caption">
                      {cam.name}{' '}
                      <Typography component="span" variant="caption" color="text.secondary">
                        — {r?.reason ?? 'waiting…'}
                      </Typography>
                    </Typography>
                  </Stack>
                );
              })}
            </Stack>
          )}
        </Box>
      </Stack>
      {state.alertText && (
        <Alert severity="error" sx={{ mt: 1, py: 0.5 }}>
          {state.alertText}
        </Alert>
      )}
    </Box>
  );
}

// -------------------------------------------------------------------------
// Main modal
// -------------------------------------------------------------------------
export function HealthCheckModal({
  open,
  onClose,
  readiness,
}: {
  open: boolean;
  onClose: () => void;
  readiness: Record<string, CameraReadiness>;
}) {
  const [internet, setInternet] = useState<CheckState>({ ...INITIAL, maxAttempts: 9 });
  const [server, setServer]     = useState<CheckState>({ ...INITIAL, maxAttempts: 3 });
  const [cameras, setCameras]   = useState<CheckState>({ ...INITIAL });
  const [cameraNames, setCameraNames] = useState<{ id: string; name: string }[]>([]);
  const [running, setRunning]   = useState(false);
  const [allPassed, setAllPassed] = useState(false);
  const abortRef = useRef(false);

  // ---- helpers ------------------------------------------------------------

  const reset = useCallback(() => {
    abortRef.current = false;
    setInternet({ ...INITIAL, maxAttempts: 9 });
    setServer({ ...INITIAL, maxAttempts: 3 });
    setCameras({ ...INITIAL });
    setAllPassed(false);
  }, []);

  // ---- run checks ---------------------------------------------------------
  const runChecks = useCallback(async () => {
    if (running) return;
    abortRef.current = false;
    setRunning(true);
    setAllPassed(false);

    // ── 1. Internet ──────────────────────────────────────────────────────
    setInternet((s) => ({ ...s, status: 'checking', attempt: 1, alertText: null }));

    let iResult: HealthCheckResult;
    try {
      iResult = await window.bumper.checkInternet(9);
    } catch {
      iResult = { ok: false, message: 'Check failed unexpectedly', attempts: 9 };
    }

    if (abortRef.current) { setRunning(false); return; }

    setInternet({
      status: iResult.ok ? 'pass' : 'fail',
      attempt: iResult.attempts,
      maxAttempts: 9,
      message: iResult.ok ? 'Connected' : `Failed after ${iResult.attempts} attempts`,
      alertText: iResult.ok ? null : 'Please connect to the internet',
    });

    if (!iResult.ok) {
      setServer((s) => ({ ...s, status: 'skipped' }));
      setCameras((s) => ({ ...s, status: 'skipped' }));
      setRunning(false);
      return;
    }

    // ── 2. Moments Server ────────────────────────────────────────────────
    setServer((s) => ({ ...s, status: 'checking', attempt: 1, alertText: null }));

    let sResult: HealthCheckResult;
    try {
      sResult = await window.bumper.checkServer(3);
    } catch {
      sResult = { ok: false, message: 'Check failed unexpectedly', attempts: 3 };
    }

    if (abortRef.current) { setRunning(false); return; }

    setServer({
      status: sResult.ok ? 'pass' : 'fail',
      attempt: sResult.attempts,
      maxAttempts: 3,
      message: sResult.ok ? 'Server is active' : `Failed after ${sResult.attempts} attempts`,
      alertText: sResult.ok ? null : 'Contact FOG Support',
    });

    if (!sResult.ok) {
      setCameras((s) => ({ ...s, status: 'skipped' }));
      setRunning(false);
      return;
    }

    // ── 3. Cameras ───────────────────────────────────────────────────────
    let camCfg: { total: number; cameras: { id: string; name: string }[] };
    try {
      camCfg = await window.bumper.getCameraConfig();
    } catch {
      camCfg = { total: 0, cameras: [] };
    }

    setCameraNames(camCfg.cameras);
    setCameras((s) => ({ ...s, status: 'checking', maxAttempts: camCfg.total }));

    // Poll readiness for up to 15 s — cameras appear in the shared `readiness` prop
    const deadline = Date.now() + 15_000;
    let cameraOk = false;
    while (Date.now() < deadline && !abortRef.current) {
      const readyCount = camCfg.cameras.filter((c) => readiness[c.id]?.ready).length;
      if (readyCount >= camCfg.total && camCfg.total > 0) {
        cameraOk = true;
        break;
      }
      await new Promise((r) => setTimeout(r, 500));
    }

    if (abortRef.current) { setRunning(false); return; }

    const readyCount = camCfg.cameras.filter((c) => readiness[c.id]?.ready).length;
    const total = camCfg.total;
    setCameras({
      status: cameraOk ? 'pass' : 'fail',
      attempt: readyCount,
      maxAttempts: total,
      message: cameraOk ? `${total}/${total} cameras ready` : `Only ${readyCount}/${total} cameras ready`,
      alertText: cameraOk ? null : `Insufficient cameras — only ${readyCount}/${total} connected`,
    });

    if (cameraOk) setAllPassed(true);
    setRunning(false);
  }, [running, readiness]);

  // Run automatically when modal first opens
  const hasRun = useRef(false);
  useEffect(() => {
    if (open && !hasRun.current) {
      hasRun.current = true;
      void runChecks();
    }
    if (!open) {
      hasRun.current = false;
      abortRef.current = true;
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRetry = () => {
    reset();
    setTimeout(() => void runChecks(), 100);
  };

  const handleClose = () => {
    abortRef.current = true;
    onClose();
  };

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  return (
    <Dialog open={open} maxWidth="sm" fullWidth disableEscapeKeyDown={running}>
      <DialogTitle sx={{ pb: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          {running && <CircularProgress size={18} />}
          <span>System Health Check</span>
        </Stack>
      </DialogTitle>

      <DialogContent dividers>
        {allPassed && (
          <Alert severity="success" sx={{ mb: 2 }}>
            All systems operational — Bumper Car Moments is ready!
          </Alert>
        )}

        <Stack spacing={0} divider={<Divider flexItem sx={{ my: 0.5 }} />}>
          {/* Internet */}
          <CheckRow
            label="Internet Connectivity"
            state={internet}
            description={`Checks network reachability — up to ${internet.maxAttempts} attempts`}
          />

          {/* Server */}
          <CheckRow
            label="Bumper Car Moments Server"
            state={server}
            description={`Verifies the moments server is active — up to ${server.maxAttempts} attempts`}
          />

          {/* Cameras */}
          <CameraCheckRow
            state={cameras}
            cameraNames={cameraNames}
            readiness={readiness}
          />
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 1.5, gap: 1 }}>
        <Tooltip title={running ? 'Checks in progress…' : ''}>
          <span>
            <Button
              variant="outlined"
              onClick={handleRetry}
              disabled={running}
            >
              Re-run Checks
            </Button>
          </span>
        </Tooltip>
        <Button
          variant="contained"
          onClick={handleClose}
          color={allPassed ? 'success' : 'primary'}
          disabled={running}
        >
          {allPassed ? 'Continue' : 'Dismiss'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
