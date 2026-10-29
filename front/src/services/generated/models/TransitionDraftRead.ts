/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TransitionDocument } from './TransitionDocument';
import type { TransitionIssue } from './TransitionIssue';
import type { TransitionPublicationSummary } from './TransitionPublicationSummary';
/**
 * Controlled v2 form and overlap/anchor checklist; availability is not a model assessment.
 */
export type TransitionDraftRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    base_publication_id: string;
    base_publication_hash: string;
    revision: number;
    document: TransitionDocument;
    base_duration_ms: number;
    overlap_ms: number;
    duration_ms: number;
    can_freeze: boolean;
    issues: Array<TransitionIssue>;
    publications: Array<TransitionPublicationSummary>;
};

