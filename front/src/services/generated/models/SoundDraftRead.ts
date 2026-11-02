/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditIssue } from './EditIssue';
import type { SoundDocument_Output } from './SoundDocument_Output';
import type { SoundPublicationSummary } from './SoundPublicationSummary';
/**
 * Current form and actual picture/audio readiness, without private file paths.
 */
export type SoundDraftRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    revision: number;
    document: SoundDocument_Output;
    duration_ms: number;
    can_freeze: boolean;
    issues: Array<EditIssue>;
    changes: Array<string>;
    publications: Array<SoundPublicationSummary>;
};

