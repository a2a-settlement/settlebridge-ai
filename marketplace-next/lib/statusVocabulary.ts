import {
  AlertCircle,
  Ban,
  CheckCircle2,
  Circle,
  CircleDashed,
  CircleDot,
  CircleHelp,
  Clock,
  Eye,
  FolderGit2,
  Lock,
  PauseCircle,
  Plug,
  Scale,
  Undo2,
  Wallet,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { CyberFundingState, RepoStatus, StepState, WorkView } from "@/lib/cyberContracts";
import {
  fundingLabels,
  repoStatusLabels,
  roleLabels,
  stepLabels,
  type StatusTone,
} from "./statusLabels";

export interface StatusVisual extends StatusTone {
  icon: LucideIcon;
}

function withIcon(icon: LucideIcon, tone: StatusTone): StatusVisual {
  return { icon, label: tone.label, textClass: tone.textClass };
}

export const stepStatus = {
  done: withIcon(CheckCircle2, stepLabels.done),
  in_progress: withIcon(CircleDot, stepLabels.in_progress),
  not_started: withIcon(Circle, stepLabels.not_started),
  blocked: withIcon(Ban, stepLabels.blocked),
  inconclusive: withIcon(CircleHelp, stepLabels.inconclusive),
  not_assessed: withIcon(CircleDashed, stepLabels.not_assessed),
  needs_decision: withIcon(AlertCircle, stepLabels.needs_decision),
} satisfies Record<StepState, StatusVisual>;

export const repoStatus = {
  needs_attention: withIcon(AlertCircle, repoStatusLabels.needs_attention),
  task_in_progress: withIcon(CircleDot, repoStatusLabels.task_in_progress),
  no_open_tasks: withIcon(Circle, repoStatusLabels.no_open_tasks),
  paused: withIcon(PauseCircle, repoStatusLabels.paused),
  setup_needed: withIcon(Plug, repoStatusLabels.setup_needed),
  not_assessed: withIcon(CircleDashed, repoStatusLabels.not_assessed),
} satisfies Record<RepoStatus, StatusVisual>;

export const fundingStatus = {
  balance_checked: withIcon(Scale, fundingLabels.balance_checked),
  funds_held: withIcon(Lock, fundingLabels.funds_held),
  payment_pending: withIcon(Clock, fundingLabels.payment_pending),
  payment_review: withIcon(Eye, fundingLabels.payment_review),
  paid: withIcon(CheckCircle2, fundingLabels.paid),
  not_paid: withIcon(Undo2, fundingLabels.not_paid),
} satisfies Record<CyberFundingState, StatusVisual>;

export const roleSwitch = {
  fund: { icon: Wallet, label: roleLabels.fund },
  solve: { icon: Wrench, label: roleLabels.solve },
} satisfies Record<WorkView, { icon: LucideIcon; label: string }>;

export const repoIcon = FolderGit2;
