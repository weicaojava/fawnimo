/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { IdentityUser } from './IdentityUser';
import type { IdentityWorkspace } from './IdentityWorkspace';
import type { TrialAccessRead } from './TrialAccessRead';
/**
 * An authenticated principal and the exact active scope for stale-tab protection.
 */
export type IdentityRead = {
    user: IdentityUser;
    workspace_id: string;
    role: 'owner' | 'editor' | 'viewer';
    workspaces: Array<IdentityWorkspace>;
    trial?: (TrialAccessRead | null);
};

