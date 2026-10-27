/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanIssue } from './PlanIssue';
/**
 * Separate content completeness from unavailable runtime prerequisites.
 */
export type PlanReadiness = {
    can_compile: boolean;
    issues: Array<PlanIssue>;
    runtime_issues: Array<PlanIssue>;
};

