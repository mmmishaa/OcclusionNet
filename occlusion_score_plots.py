import matplotlib.lines as mlines
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

sns.set_style("whitegrid")

df_b0 = pd.read_csv('results_b0.csv')

try:
    df_b5 = pd.read_csv('results_b5.csv')
except Exception:
    df_b5 = None

def plot_condition(condition_name, condition_label):
    fig, axes = plt.subplots(1, 2, figsize=(16, 5), dpi=130)
    sub_b0 = df_b0[df_b0['condition'] == condition_name]

    if df_b5 is not None:
        sub_b5 = df_b5[df_b5['condition'] == condition_name]
        merged = pd.merge(
            sub_b0[['name', 'conf_clear', 'conf_adverse']],
            sub_b5[['name', 'conf_clear', 'conf_adverse']],
            on='name',
            suffixes=('_b0', '_b5')
        )
        merged['diff_clear'] = merged['conf_clear_b0'] - merged['conf_clear_b5']
        merged['diff_adverse'] = merged['conf_adverse_b0'] - merged['conf_adverse_b5']

        sns.histplot(
            data=merged[['diff_clear', 'diff_adverse']],
            bins=25,
            kde=True,
            palette=['#4C72B0', '#DD8452'],
            alpha=0.5,
            element="step",
            ax=axes[0],
            legend=False
        )

        mean_diff_clr = merged['diff_clear'].mean()
        mean_diff_adv = merged['diff_adverse'].mean()

        axes[0].axvline(mean_diff_clr, color='#4C72B0', linestyle='--')
        axes[0].axvline(mean_diff_adv, color='#DD8452', linestyle='--')
        axes[0].axvline(0, color='black', linestyle=':', alpha=0.7)

        handles_left = [
            mpatches.Patch(color='#4C72B0', alpha=0.5, label='Clear (до окклюзии)'),
            mpatches.Patch(color='#DD8452', alpha=0.5, label=f'Adverse ({condition_label})'),
            mlines.Line2D([], [], color='#4C72B0', linestyle='--', label=f'Mean Clear: {mean_diff_clr:.4f}'),
            mlines.Line2D([], [], color='#DD8452', linestyle='--', label=f'Mean Adverse: {mean_diff_adv:.4f}'),
            mlines.Line2D([], [], color='black', linestyle=':', label='Zero (B0 = B5)')
        ]
        axes[0].legend(handles=handles_left, title='Условие / Среднее', fontsize=9)
        axes[0].set_title(f'Разность уверенности (B0 - B5): {condition_label}', fontweight='bold', fontsize=12)
        axes[0].set_xlabel('Разность уверенности (B0 - B5)', fontsize=10)
    else:
        axes[0].text(0.5, 0.5, 'results_b5.csv не найден', ha='center', va='center')
        axes[0].set_title(f'Сравнение B0 и B5: {condition_label} (нет results_b5.csv)', fontweight='bold', fontsize=12)

    sns.histplot(
        data=sub_b0[['conf_clear', 'conf_adverse']], 
        bins=25, 
        kde=True, 
        palette=['#4C72B0', '#DD8452'],
        alpha=0.6,
        element="step",
        ax=axes[1],
        legend=False
    )

    mean_clr = sub_b0['conf_clear'].mean()
    mean_adv = sub_b0['conf_adverse'].mean()
    axes[1].axvline(mean_clr, color='#4C72B0', linestyle='--')
    axes[1].axvline(mean_adv, color='#DD8452', linestyle='--')

    handles_right = [
        mpatches.Patch(color='#4C72B0', alpha=0.6, label='Clear (до окклюзии)'),
        mpatches.Patch(color='#DD8452', alpha=0.6, label=f'Adverse ({condition_label})'),
        mlines.Line2D([], [], color='#4C72B0', linestyle='--', label=f'Mean Clear: {mean_clr:.3f}'),
        mlines.Line2D([], [], color='#DD8452', linestyle='--', label=f'Mean Adverse: {mean_adv:.3f}')
    ]
    axes[1].legend(handles=handles_right, title='Условие / Среднее', fontsize=9)
    axes[1].set_title(f'B0: Уверенность до и после окклюзии ({condition_label})', fontweight='bold', fontsize=12)
    axes[1].set_xlabel('Средняя максимальная уверенность кадра', fontsize=10)

    plt.tight_layout()

plot_condition('fog', 'туман')
plot_condition('night', 'ночь')

plt.show()