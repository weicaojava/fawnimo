/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanIssue } from './PlanIssue';
import type { Seedance25PreviewEntry } from './Seedance25PreviewEntry';
/**
 * A deterministic inert preview cannot submit or confer any model qualification.
 */
export type Seedance25PlanPreview = {
    plan_id: string;
    content_hash: string;
    profile_hash: string;
    wire_hash: (string | null);
    can_prepare: boolean;
    can_submit?: boolean;
    execution_enabled?: boolean;
    model_qualified?: boolean;
    issues: Array<PlanIssue>;
    entries: Array<Seedance25PreviewEntry>;
};

