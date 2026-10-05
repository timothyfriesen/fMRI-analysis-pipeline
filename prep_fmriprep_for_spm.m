%% prep_fmriprep_for_spm.m
% Prepares fMRIPrep outputs for an SPM first-level GLM:
%   1) unzips the preprocessed MNI-space BOLD runs
%   2) smooths them (SPM does this; fMRIPrep does not)
%   3) builds a confound regressor file per run for SPM's "multiple regressors"
%   4) prints mean framewise displacement per run as a quick motion check
%
% Requires SPM (12 or 25) on the MATLAB path.

clear; clc;

%% ---- Settings (edit these) ---------------------------------------------
deriv   = 'C:\Users\user\Documents\fmriprep\derivatives_test';  % fMRIPrep output folder
outdir  = 'C:\Users\user\Documents\fmriprep\spm_prep';          % where SPM-ready files go
sub     = '01';
task    = 'traintest3';
runs    = [1 2];
fwhm    = [6 6 6];     % smoothing kernel in mm
n_acc   = 5;           % number of aCompCor components to include

%% ---- Setup ---------------------------------------------------------------
spm('defaults', 'fmri');
spm_jobman('initcfg');
funcdir = fullfile(deriv, ['sub-' sub], 'func');
subout  = fullfile(outdir, ['sub-' sub]);
if ~exist(subout, 'dir'), mkdir(subout); end

for r = runs
    base = sprintf('sub-%s_task-%s_run-%d', sub, task, r);
    fprintf('\n=== %s ===\n', base);

    %% 1) Unzip preprocessed BOLD (MNI space)
    f = dir(fullfile(funcdir, [base '_space-MNI152NLin2009cAsym*_desc-preproc_bold.nii.gz']));
    if isempty(f)
        warning('No preprocessed BOLD found for %s, skipping.', base); continue;
    end
    gunzip(fullfile(funcdir, f(1).name), subout);
    niiname = erase(f(1).name, '.gz');
    fprintf('Unzipped: %s\n', niiname);

    %% 2) Smooth (output gets prefix "s")
    vols = cellstr(spm_select('ExtFPList', subout, ['^' regexptranslate('escape', niiname) '$'], Inf));
    clear matlabbatch;
    matlabbatch{1}.spm.spatial.smooth.data   = vols;
    matlabbatch{1}.spm.spatial.smooth.fwhm   = fwhm;
    matlabbatch{1}.spm.spatial.smooth.dtype  = 0;
    matlabbatch{1}.spm.spatial.smooth.im     = 0;
    matlabbatch{1}.spm.spatial.smooth.prefix = 's';
    spm_jobman('run', matlabbatch);
    fprintf('Smoothed: s%s (%d volumes)\n', niiname, numel(vols));

    %% 3) Confound regressors
    conf_file = fullfile(funcdir, [base '_desc-confounds_timeseries.tsv']);
    T = readtable(conf_file, 'FileType', 'text', 'Delimiter', '\t', ...
                  'TreatAsMissing', 'n/a', 'VariableNamingRule', 'preserve');
    vn = T.Properties.VariableNames;

    motion  = {'trans_x','trans_y','trans_z','rot_x','rot_y','rot_z'};
    derivs  = strcat(motion, '_derivative1');
    acc     = arrayfun(@(k) sprintf('a_comp_cor_%02d', k), 0:n_acc-1, 'UniformOutput', false);
    spikes  = vn(startsWith(vn, 'motion_outlier') | startsWith(vn, 'non_steady_state_outlier'));

    wanted  = [motion, derivs, acc, spikes];
    keep    = wanted(ismember(wanted, vn));
    missing = setdiff(wanted, keep);
    if ~isempty(missing)
        fprintf('Note: columns not found and skipped: %s\n', strjoin(missing, ', '));
    end

    R = table2array(T(:, keep));
    R(isnan(R)) = 0;          % first row of derivatives is n/a -> 0
    names = keep;             %#ok<NASGU> saved with R for reference
    save(fullfile(subout, [base '_confounds_spm.mat']), 'R', 'names');
    fprintf('Confounds: %d regressors (%d spike/non-steady-state)\n', size(R,2), numel(spikes));

    %% 4) Quick motion check
    if ismember('framewise_displacement', vn)
        fd = T.framewise_displacement;
        fprintf('Mean FD: %.3f mm | max FD: %.3f mm | volumes FD > 0.5 mm: %d of %d\n', ...
            mean(fd, 'omitnan'), max(fd), sum(fd > 0.5), height(T));
    end
end

fprintf('\nDone. SPM-ready files are in:\n  %s\n', subout);
fprintf('Use the s*.nii files as scans and *_confounds_spm.mat as "Multiple regressors".\n');
