/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanIssue } from './PlanIssue';
/**
 * Current preparation and advisory runtime checks; submission rechecks atomically.
 */
export type PlanPreflight = {
    usable: boolean;
    can_submit: boolean;
    issues: Array<PlanIssue>;
    runtime_issues: Array<PlanIssue>;
};

