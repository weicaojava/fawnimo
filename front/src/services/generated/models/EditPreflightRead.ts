/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditIssue } from './EditIssue';
import type { EditSelectionDifference } from './EditSelectionDifference';
/**
 * Check source availability independently of present selection choices.
 */
export type EditPreflightRead = {
    usable: boolean;
    issues: Array<EditIssue>;
    current_selection_differences: Array<EditSelectionDifference>;
    human_declarations_only?: boolean;
};

