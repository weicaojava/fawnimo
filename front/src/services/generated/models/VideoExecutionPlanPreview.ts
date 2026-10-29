/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanIssue } from './PlanIssue';
import type { VideoExecutionPreviewEntry } from './VideoExecutionPreviewEntry';
/**
 * A read-only executable-profile preview never grants submission or quality qualification.
 */
export type VideoExecutionPlanPreview = {
    plan_id: string;
    content_hash: string;
    wire_hash?: (string | null);
    can_prepare: boolean;
    can_submit?: boolean;
    execution_enabled: boolean;
    model_qualified?: boolean;
    issues: Array<PlanIssue>;
    entries: Array<VideoExecutionPreviewEntry>;
};

