/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Issue } from './Issue';
/**
 * Human-declared checklist completeness, not model or license verification.
 */
export type Readiness = {
    can_freeze: boolean;
    issues: Array<Issue>;
    semantic_verification?: string;
};

