/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditIssue } from './EditIssue';
/**
 * Historical choice with explicit eligibility for a selected plan.
 */
export type EditTakeOption = {
    id: string;
    media_id: string;
    name: string;
    origin_kind: string;
    plan_id: (string | null);
    entry_hash: (string | null);
    eligible_for_plan: boolean;
    issues: Array<EditIssue>;
    duration_ms: (number | null);
};

